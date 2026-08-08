"""
Dashboard slice: the admin dashboard HTML page and its stats API.

The stats API mirrors the PHP `admin/api/dashboard.php`. Order revenue is
computed from order items as `sale_price_per_kg * quantity / 1000` (quantity
is grams), matching the PHP. Everything is scoped to the active brand unless
the caller passes `scope=all` and is allowed multiple brands (super_admin or
an admin with dashboard/all permission on 2+ brands).
"""
from datetime import timedelta

from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    Sum,
)
from django.db.models.functions import Coalesce, TruncDate, TruncMonth
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from accounts.models import User
from core.api import HasModulePermission, current_brand_id, ok
from core.auth import admin_login_required
from marketing.models import Review
from orders.models import Order, OrderItem
from products.models import Flavor

# oi.quantity (grams) * oi.sale_price_per_kg / 1000 == rupees for the line.
REVENUE = ExpressionWrapper(
    F('quantity') * F('sale_price_per_kg') / 1000.0,
    output_field=FloatField(),
)
INACTIVE_STATUSES = ['cancelled', 'deleted']


@admin_login_required
@ensure_csrf_cookie
def dashboard_page(request):
    return render(request, 'admin/dashboard.html')


def _pct_change(curr, prev):
    if not prev:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


class DashboardStatsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'dashboard'

    def _brand_ids(self, request):
        """
        Returns the list of brand ids to scope to, or None for "no filter"
        (all brands). Mirrors the PHP scope=brand|all logic.
        """
        scope = request.query_params.get('scope', 'brand')
        user = request.user
        if scope == 'all':
            if getattr(user, 'is_super_admin', False):
                return None
            allowed = [
                int(bid)
                for bid, perms in (user.brand_permissions or {}).items()
                if 'all' in perms or 'dashboard' in perms
            ]
            if len(allowed) >= 2:
                return allowed
        return [current_brand_id(request)]

    @staticmethod
    def _scoped(qs, brand_ids, field='brand_id'):
        if brand_ids is None:
            return qs
        return qs.filter(**{f'{field}__in': brand_ids})

    @staticmethod
    def _totals(order_qs):
        count = order_qs.count()
        revenue = OrderItem.objects.filter(order__in=order_qs).aggregate(
            r=Sum(REVENUE),
        )['r'] or 0.0
        return count, float(revenue)

    def get(self, request):
        brand_ids = self._brand_ids(request)
        now = timezone.localtime()
        today = timezone.localdate()

        active = self._scoped(
            Order.objects.exclude(status__in=INACTIVE_STATUSES), brand_ids,
        )

        today_orders, today_rev = self._totals(active.filter(order_date__date=today))

        this_month_qs = active.filter(order_date__year=now.year, order_date__month=now.month)
        month_orders, month_rev = self._totals(this_month_qs)

        prev_month_date = today.replace(day=1) - timedelta(days=1)
        last_month_qs = active.filter(
            order_date__year=prev_month_date.year,
            order_date__month=prev_month_date.month,
        )
        last_orders, last_rev = self._totals(last_month_qs)

        all_orders, all_rev = self._totals(active)
        avg_order_value = round(all_rev / all_orders) if all_orders else 0

        completed_orders = self._scoped(
            Order.objects.filter(status='delivered'), brand_ids,
        ).count()

        total_users = self._scoped(User.objects.all(), brand_ids).count()

        order_status = {
            r['status']: r['cnt']
            for r in self._scoped(Order.objects.exclude(status='deleted'), brand_ids)
            .values('status').annotate(cnt=Count('id'))
        }

        pending_reviews = self._scoped(
            Review.objects.filter(is_approved=False), brand_ids,
        ).count()

        data = {
            'scope': request.query_params.get('scope', 'brand'),
            'today': {'orders': today_orders, 'revenue': today_rev},
            'this_month': {
                'orders': month_orders,
                'revenue': month_rev,
                'orders_vs_last': _pct_change(month_orders, last_orders),
                'revenue_vs_last': _pct_change(month_rev, last_rev),
            },
            'avg_order_value': avg_order_value,
            'all_time_revenue': all_rev,
            'completed_orders': completed_orders,
            'total_users': total_users,
            'order_status': order_status,
            'pending_reviews': pending_reviews,
            'low_stock': self._low_stock(brand_ids),
            'trend': self._trend(request, active),
            'top_flavors': self._top_flavors(active),
            'recent_orders': self._recent_orders(brand_ids),
        }
        return ok(data)

    def _low_stock(self, brand_ids):
        """
        Approximation of the PHP low-stock query. The PHP `stock` table carried
        a per-flavor `reorder_level_grams`; here stock is batched, so quantity
        is summed per flavor and compared against `Flavor.reorder_level_grams`.
        Flavor and Stock are global now, so this is not brand-scoped (brand_ids
        is unused but kept for signature compatibility with other _*(brand_ids) helpers).
        """
        flavors = (
            Flavor.objects.filter(is_active=True, reorder_level_grams__gt=0)
            .annotate(total_qty=Coalesce(Sum('stock_batches__quantity_grams'), 0))
            .filter(total_qty__lte=F('reorder_level_grams'))
            .annotate(ratio=ExpressionWrapper(
                F('total_qty') * 1.0 / F('reorder_level_grams'),
                output_field=FloatField(),
            ))
            .order_by('ratio')[:5]
        )
        return [
            {
                'name': f.name,
                'quantity_grams': f.total_qty,
                'reorder_level_grams': f.reorder_level_grams,
                'qty_kg': round(f.total_qty / 1000, 2),
                'reorder_kg': round(f.reorder_level_grams / 1000, 2),
            }
            for f in flavors
        ]

    def _trend(self, request, active):
        period = request.query_params.get('period', '30d')
        if period == 'all':
            return self._monthly_trend(active)
        if period == 'range':
            date_from = parse_date(request.query_params.get('from', '') or '')
            date_to = parse_date(request.query_params.get('to', '') or '')
            if date_from and date_to:
                return self._daily_trend(active, date_from, date_to)
        today = timezone.localdate()
        return self._daily_trend(active, today - timedelta(days=29), today)

    def _daily_trend(self, active, start, end):
        period_qs = active.filter(order_date__date__gte=start, order_date__date__lte=end)
        orders_by_day = {
            r['d']: r['cnt']
            for r in period_qs.annotate(d=TruncDate('order_date'))
            .values('d').annotate(cnt=Count('id', distinct=True))
        }
        rev_by_day = {
            r['d']: float(r['rev'] or 0)
            for r in OrderItem.objects.filter(order__in=period_qs)
            .annotate(d=TruncDate('order__order_date'))
            .values('d').annotate(rev=Sum(REVENUE))
        }
        trend = []
        cur = start
        while cur <= end:
            trend.append({
                'date': cur.isoformat(),
                'label': cur.strftime('%d %b'),
                'orders': orders_by_day.get(cur, 0),
                'revenue': rev_by_day.get(cur, 0.0),
            })
            cur += timedelta(days=1)
        return trend

    def _monthly_trend(self, active):
        orders_by_month = {
            r['m']: r['cnt']
            for r in active.annotate(m=TruncMonth('order_date'))
            .values('m').annotate(cnt=Count('id', distinct=True))
        }
        rev_by_month = {
            r['m']: float(r['rev'] or 0)
            for r in OrderItem.objects.filter(order__in=active)
            .annotate(m=TruncMonth('order__order_date'))
            .values('m').annotate(rev=Sum(REVENUE))
        }
        months = sorted(set(orders_by_month) | set(rev_by_month))
        return [
            {
                'date': m.strftime('%Y-%m'),
                'label': m.strftime('%b %Y'),
                'orders': orders_by_month.get(m, 0),
                'revenue': rev_by_month.get(m, 0.0),
            }
            for m in months
        ]

    @staticmethod
    def _top_flavors(active):
        rows = (
            OrderItem.objects.filter(order__in=active)
            .values('flavor_name')
            .annotate(
                total_grams=Sum('quantity'),
                order_count=Count('order', distinct=True),
                revenue=Sum(REVENUE),
            )
            .order_by('-total_grams')[:5]
        )
        return [
            {
                'name': r['flavor_name'],
                'total_grams': int(r['total_grams'] or 0),
                'order_count': r['order_count'],
                'revenue': float(r['revenue'] or 0),
            }
            for r in rows
        ]

    def _recent_orders(self, brand_ids):
        orders = (
            self._scoped(Order.objects.exclude(status='deleted'), brand_ids)
            .select_related('user')
            .prefetch_related('items')
            .order_by('-order_date')[:10]
        )
        recent = []
        for o in orders:
            items = list(o.items.all())
            total = sum(i.quantity * i.sale_price_per_kg / 1000 for i in items)
            names = ', '.join(i.flavor_name for i in sorted(items, key=lambda i: i.id))
            recent.append({
                'id': o.id,
                'status': o.status,
                'order_date': o.order_date.isoformat(),
                'customer_name': o.name or (o.user.name if o.user else '') or 'Guest',
                'total': float(total),
                'items': names,
            })
        return recent
