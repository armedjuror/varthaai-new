"""
B2B dashboard slice: the overview HTML page and its stats API.

Mirrors the `view=dashboard` branch of the PHP `admin/api/b2b.php`. Everything
is scoped to the active brand via `current_brand_id`. Money figures are derived
from `total_amount - paid_amount` (never the stored `balance_amount`):

  - outstanding = sum of positive balances over non-cancelled orders
  - overdue     = that outstanding restricted to a past due_date
  - advance     = abs(sum of negative balances) i.e. overpayment credit
"""
from datetime import timedelta

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, ok
from core.auth import admin_login_required, require_module
from crm.models import B2BActivity, B2BCompany
from orders.models import B2BOrder

ACTIVE_STATUSES = ['draft', 'confirmed', 'dispatched']
MONEY = DecimalField(max_digits=14, decimal_places=2)
BALANCE = F('total_amount') - F('paid_amount')


@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def b2b_dashboard_page(request):
    return render(request, 'admin/b2b-dashboard.html')


def _f(value):
    """Decimal/None -> float for JSON."""
    return float(value) if value is not None else 0.0


def _pct_change(curr, prev):
    if not prev:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


class B2BDashboardStatsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    def get(self, request):
        brand_id = current_brand_id(request)
        today = timezone.localdate()

        return ok({
            'pipeline': self._pipeline(brand_id),
            'order_stats': self._order_stats(brand_id, today),
            'revenue_stats': self._revenue_stats(brand_id, today),
            'revenue_trend': self._revenue_trend(brand_id, today),
            'follow_ups': self._follow_ups(brand_id, today),
            'overdue_payments': self._overdue_payments(brand_id, today),
            'pending_orders': self._pending_orders(brand_id),
            'top_companies': self._top_companies(brand_id),
        })

    @staticmethod
    def _pipeline(brand_id):
        rows = (
            B2BCompany.objects.filter(brand_id=brand_id, is_active=True)
            .values('stage').annotate(cnt=Count('id'))
        )
        return {r['stage']: r['cnt'] for r in rows}

    @staticmethod
    def _order_stats(brand_id, today):
        orders = (
            B2BOrder.objects.filter(brand_id=brand_id)
            .exclude(status='cancelled')
            .annotate(balance=BALANCE)
        )
        agg = orders.aggregate(
            total_revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
            total_outstanding=Coalesce(
                Sum('balance', filter=Q(balance__gt=0)), Value(0), output_field=MONEY,
            ),
            total_overdue=Coalesce(
                Sum('balance', filter=Q(balance__gt=0, due_date__lt=today)),
                Value(0), output_field=MONEY,
            ),
            total_advance=Coalesce(
                Sum(Case(When(balance__lt=0, then=-F('balance')), output_field=MONEY)),
                Value(0), output_field=MONEY,
            ),
        )
        pending = B2BOrder.objects.filter(
            brand_id=brand_id, status__in=ACTIVE_STATUSES,
        ).count()
        return {
            'pending_orders': pending,
            'total_revenue': _f(agg['total_revenue']),
            'total_outstanding': _f(agg['total_outstanding']),
            'total_overdue': _f(agg['total_overdue']),
            'total_advance': _f(agg['total_advance']),
        }

    @staticmethod
    def _revenue_stats(brand_id, today):
        """
        Today / this-month / all-time revenue + AOV, mirroring the B2C
        dashboard's KPI shape but computed from B2BOrder.total_amount.
        """
        active = B2BOrder.objects.filter(brand_id=brand_id).exclude(status='cancelled')

        def totals(qs):
            agg = qs.aggregate(
                rev=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
                cnt=Count('id'),
            )
            return _f(agg['rev']), agg['cnt']

        today_rev, today_orders = totals(active.filter(order_date__date=today))

        now = timezone.localtime()
        month_rev, month_orders = totals(
            active.filter(order_date__year=now.year, order_date__month=now.month),
        )

        prev_month_date = today.replace(day=1) - timedelta(days=1)
        last_rev, last_orders = totals(active.filter(
            order_date__year=prev_month_date.year,
            order_date__month=prev_month_date.month,
        ))

        all_rev, all_orders = totals(active)
        aov = round(all_rev / all_orders) if all_orders else 0

        return {
            'today': {'revenue': today_rev, 'orders': today_orders},
            'this_month': {
                'revenue': month_rev,
                'orders': month_orders,
                'revenue_vs_last': _pct_change(month_rev, last_rev),
                'orders_vs_last': _pct_change(month_orders, last_orders),
            },
            'all_time_revenue': all_rev,
            'avg_order_value': aov,
        }

    @staticmethod
    def _revenue_trend(brand_id, today):
        """
        Daily revenue for the last 30 days, split into paid vs pending
        (revenue - paid_amount, floored at 0) so the chart can stack them.
        """
        start = today - timedelta(days=29)
        rows = (
            B2BOrder.objects.filter(
                brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=today,
            )
            .exclude(status='cancelled')
            .annotate(d=TruncDate('order_date'))
            .values('d')
            .annotate(
                revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
                paid=Coalesce(Sum('paid_amount'), Value(0), output_field=MONEY),
                orders=Count('id'),
            )
        )
        by_day = {r['d']: r for r in rows}

        trend = []
        cur = start
        while cur <= today:
            r = by_day.get(cur)
            revenue = _f(r['revenue']) if r else 0.0
            paid = _f(r['paid']) if r else 0.0
            trend.append({
                'date': cur.isoformat(),
                'label': cur.strftime('%d %b'),
                'revenue': revenue,
                'paid': paid,
                'pending': max(revenue - paid, 0.0),
                'orders': r['orders'] if r else 0,
            })
            cur += timedelta(days=1)
        return trend

    @staticmethod
    def _follow_ups(brand_id, today):
        activities = (
            B2BActivity.objects.filter(
                company__brand_id=brand_id,
                follow_up_date__isnull=False,
                follow_up_date__lte=today,
                is_follow_up_done=False,
            )
            .select_related('company')
            .order_by('follow_up_date')[:20]
        )
        return [
            {
                'id': a.id,
                'company_id': a.company_id,
                'company_name': a.company.company_name,
                'type': a.type,
                'subject': a.subject,
                'description': a.description,
                'follow_up_date': a.follow_up_date.isoformat(),
            }
            for a in activities
        ]

    @staticmethod
    def _overdue_payments(brand_id, today):
        orders = (
            B2BOrder.objects.filter(brand_id=brand_id)
            .exclude(status='cancelled')
            .annotate(balance=BALANCE)
            .filter(balance__gt=0)
            .filter(Q(due_date__isnull=True) | Q(due_date__lt=today))
            .select_related('company')
            .order_by(F('due_date').asc(nulls_last=True))[:20]
        )
        return [
            {
                'id': o.id,
                'company_id': o.company_id,
                'company_name': o.company.company_name,
                'total_amount': _f(o.total_amount),
                'paid_amount': _f(o.paid_amount),
                'balance': _f(o.balance),
                'due_date': o.due_date.isoformat() if o.due_date else None,
                'status': o.status,
            }
            for o in orders
        ]

    @staticmethod
    def _pending_orders(brand_id):
        orders = (
            B2BOrder.objects.filter(brand_id=brand_id, status__in=ACTIVE_STATUSES)
            .select_related('company')
            .order_by('-order_date')[:15]
        )
        return [
            {
                'id': o.id,
                'company_id': o.company_id,
                'company_name': o.company.company_name,
                'total_amount': _f(o.total_amount),
                'status': o.status,
                'payment_status': o.payment_status,
            }
            for o in orders
        ]

    @staticmethod
    def _top_companies(brand_id):
        not_cancelled = ~Q(orders__status='cancelled')
        companies = (
            B2BCompany.objects.filter(brand_id=brand_id, is_active=True)
            .annotate(
                revenue=Coalesce(
                    Sum('orders__total_amount', filter=not_cancelled),
                    Value(0), output_field=MONEY,
                ),
                order_count=Count('orders', filter=not_cancelled),
                outstanding=Coalesce(
                    Sum(
                        F('orders__total_amount') - F('orders__paid_amount'),
                        filter=not_cancelled & Q(
                            orders__total_amount__gt=F('orders__paid_amount'),
                        ),
                    ),
                    Value(0), output_field=MONEY,
                ),
            )
            .order_by('-revenue')[:10]
        )
        return [
            {
                'id': c.id,
                'company_name': c.company_name,
                'revenue': _f(c.revenue),
                'order_count': c.order_count,
                'outstanding': _f(c.outstanding),
            }
            for c in companies
        ]
