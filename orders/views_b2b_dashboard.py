"""
B2B dashboard slice: the overview HTML page and its stats API.

Mirrors the `view=dashboard` branch of the PHP `admin/api/b2b.php`. Everything
is scoped to the active brand via `current_brand_id`. Money figures are derived
from `total_amount - paid_amount` (never the stored `balance_amount`):

  - outstanding = sum of positive balances over non-cancelled orders
  - overdue     = that outstanding restricted to a past due_date
  - advance     = abs(sum of negative balances) i.e. overpayment credit
"""
from datetime import datetime, timedelta

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDate
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, ok
from core.auth import admin_login_required, require_module
from core.models import Brand
from crm.models import B2BActivity, B2BCompany
from orders import reports_b2b
from orders.models import B2BOrder, B2BOrderItem

ACTIVE_STATUSES = ['draft', 'confirmed', 'dispatched']
MONEY = DecimalField(max_digits=14, decimal_places=2)
BALANCE = F('total_amount') - F('paid_amount')


@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def b2b_dashboard_page(request):
    return render(request, 'admin/b2b-dashboard.html')


@admin_login_required
@require_module('b2b')
def b2b_report_page(request):
    brand_id = current_brand_id(request)
    today = timezone.localdate()
    start, end = B2BDashboardStatsAPI._trend_range(request.GET, today)
    report = reports_b2b.build_b2b_report(brand_id, start, end)

    return render(request, 'admin/b2b-report.html', {
        'brand': Brand.objects.filter(id=brand_id).first(),
        'report': report,
        'generated_at': timezone.localtime(),
    })


def _f(value):
    """Decimal/None -> float for JSON."""
    return float(value) if value is not None else 0.0


def _pct_change(curr, prev):
    if not prev:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


TREND_MAX_SPAN_DAYS = 366


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


class B2BDashboardStatsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    def get(self, request):
        brand_id = current_brand_id(request)
        today = timezone.localdate()

        trend_start, trend_end = self._trend_range(request.query_params, today)
        trend, flavor_names = self._revenue_trend(brand_id, trend_start, trend_end)

        return ok({
            'pipeline': self._pipeline(brand_id),
            'order_stats': self._order_stats(brand_id, today),
            'revenue_stats': self._revenue_stats(brand_id, today),
            'revenue_trend': trend,
            'trend_range': {'start': trend_start.isoformat(), 'end': trend_end.isoformat()},
            'flavor_names': flavor_names,
            'follow_ups': self._follow_ups(brand_id, today),
            'overdue_payments': self._overdue_payments(brand_id, today),
            'pending_orders': self._pending_orders(brand_id),
            'top_companies': self._top_companies(brand_id),
        })

    @staticmethod
    def _trend_range(params, today):
        """
        Trend date range from ?start_date=&end_date= (YYYY-MM-DD), defaulting
        to the last 30 days. Invalid/missing dates fall back to the default;
        a reversed range is swapped; the span is capped at TREND_MAX_SPAN_DAYS.
        `params` is any dict-like with `.get()` — `request.query_params` (DRF)
        or `request.GET` (plain Django view) both work.
        """
        default_start = today - timedelta(days=29)
        start = _parse_date(params.get('start_date')) or default_start
        end = _parse_date(params.get('end_date')) or today
        if start > end:
            start, end = end, start
        end = min(end, today)
        if (end - start).days > TREND_MAX_SPAN_DAYS:
            start = end - timedelta(days=TREND_MAX_SPAN_DAYS)
        return start, end

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
    def _revenue_trend(brand_id, start, end):
        """
        Daily metrics from `start` to `end` (inclusive): revenue (paid vs
        pending), order count, new leads created, leads converted, and packs
        sold per flavor that day. Returns (trend, flavor_names) —
        flavor_names sorted by total volume over the window, descending.
        """
        rows = (
            B2BOrder.objects.filter(
                brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=end,
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

        leads_rows = (
            B2BCompany.objects.filter(
                brand_id=brand_id, created_at__date__gte=start, created_at__date__lte=end,
            )
            .annotate(d=TruncDate('created_at'))
            .values('d').annotate(cnt=Count('id'))
        )
        leads_by_day = {r['d']: r['cnt'] for r in leads_rows}

        converted_rows = (
            B2BCompany.objects.filter(
                brand_id=brand_id, converted_at__date__gte=start, converted_at__date__lte=end,
            )
            .annotate(d=TruncDate('converted_at'))
            .values('d').annotate(cnt=Count('id'))
        )
        converted_by_day = {r['d']: r['cnt'] for r in converted_rows}

        pack_rows = (
            B2BOrderItem.objects.filter(
                b2b_order__brand_id=brand_id,
                b2b_order__order_date__date__gte=start,
                b2b_order__order_date__date__lte=end,
            )
            .exclude(b2b_order__status='cancelled')
            .annotate(d=TruncDate('b2b_order__order_date'))
            .values('d', 'flavor_name')
            .annotate(packs=Coalesce(Sum('quantity'), Value(0)))
        )
        packs_by_day = {}
        flavor_totals = {}
        for r in pack_rows:
            packs_by_day.setdefault(r['d'], {})[r['flavor_name']] = r['packs']
            flavor_totals[r['flavor_name']] = flavor_totals.get(r['flavor_name'], 0) + r['packs']
        flavor_names = sorted(flavor_totals, key=lambda f: -flavor_totals[f])

        trend = []
        cur = start
        while cur <= end:
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
                'leads_created': leads_by_day.get(cur, 0),
                'converted': converted_by_day.get(cur, 0),
                'packs_by_flavor': packs_by_day.get(cur, {}),
            })
            cur += timedelta(days=1)
        return trend, flavor_names

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
            .order_by('follow_up_date')
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
            .order_by(F('due_date').asc(nulls_last=True))
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
            .order_by('-order_date')
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
            .filter(Q(revenue__gt=0) | Q(order_count__gt=0))
            .order_by('-revenue')
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
