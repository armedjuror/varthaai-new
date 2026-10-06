"""
Curated business-analysis tools — the read-only surface the MCP server
exposes to Claude. Every function here is a plain Python function over the
Django ORM (never `.save()/.delete()/bulk_*`) so it can be unit-tested
without an MCP client, and registered as an MCP tool by `bizmcp/server.py`.

Scope: these report at super_admin, all-brand visibility by design — this
server is a personal business-analysis tool for the account owner, not a
multi-admin surface, so there is no per-module permission check here (that
system, in core/api.py + core/auth.py, guards the admin panel's own
sessions and is intentionally not reused for this always-superadmin path).

`brand_id=None` means "all brands combined" everywhere except
`b2b_report`, which mirrors `orders.reports_b2b.build_b2b_report` and
needs exactly one brand — it falls back to the first active Brand.

Money values are always returned as plain floats (JSON-friendly). Balance/
outstanding math is always `total_amount - paid_amount`, never the stored
`balance_amount` column, per the rest of the B2B codebase (see
`orders/views_b2b_dashboard.py`, `orders/reports_b2b.py`).
"""
from datetime import timedelta

from django.db.models import (
    Case, Count, DecimalField, ExpressionWrapper, F, FloatField, Q, Sum, Value, When,
)
from django.db.models.functions import Coalesce, TruncDate, TruncMonth, TruncWeek
from django.utils import timezone

from accounts.models import User
from core.models import Brand
from crm.models import B2BCompany
from finance.models import Expense, Income
from marketing.models import MarketingSource, Review, SourceTracking
from orders import reports_b2b
from orders.models import B2BOrder, B2BOrderItem, Coupon, Order, OrderItem
from products.models import Flavor, Stock, StockAlert
from products.services import available_grams

MONEY = DecimalField(max_digits=14, decimal_places=2)
BALANCE = F('total_amount') - F('paid_amount')
# order_items.quantity is grams; sale_price_per_kg is per-kg -> line revenue in rupees.
B2C_REVENUE = ExpressionWrapper(
    F('quantity') * F('sale_price_per_kg') / 1000.0, output_field=FloatField(),
)
B2B_INACTIVE = ['cancelled']
B2C_INACTIVE = ['cancelled', 'deleted']


def _f(value):
    return float(value) if value is not None else 0.0


def _date_range(days):
    days = max(int(days or 30), 1)
    end = timezone.localdate()
    start = end - timedelta(days=days - 1)
    return start, end


def _default_brand_id():
    b = Brand.objects.filter(is_active=True).order_by('id').first()
    return b.id if b else None


def _scope_brand(qs, brand_id, field='brand_id'):
    return qs if brand_id is None else qs.filter(**{field: brand_id})


def describe_schema():
    """Reference for the tables/columns this server can query — read this
    before writing raw SQL via run_readonly_sql, so column names are exact
    (the `readonly` DB alias is plain Postgres, not the Django ORM)."""
    return {
        'note': (
            'All amounts are stored in rupees except order_items.quantity / '
            'stock*.quantity_grams, which are grams. B2B balance is always '
            'total_amount - paid_amount, never the stored balance_amount.'
        ),
        'tables': {
            'brands': ['id', 'name', 'order_prefix', 'is_active'],
            'orders': [
                'id (varchar, e.g. VO_...)', 'brand_id', 'user_id', 'status',
                'payment_status', 'delivery_charge', 'coupon_id', 'coupon_discount',
                'order_date', 'payment_date',
            ],
            'order_items': [
                'id', 'order_id', 'flavor_id', 'flavor_pack_id', 'quantity (grams)',
                'price_per_kg', 'sale_price_per_kg', 'flavor_name', 'pack_label',
            ],
            'b2b_companies': [
                'id', 'brand_id', 'company_name', 'stage', 'source', 'assigned_to_id',
                'credit_limit', 'is_active', 'converted_at', 'created_at',
            ],
            'b2b_orders': [
                'id (varchar)', 'brand_id', 'company_id', 'status', 'payment_status',
                'total_amount', 'paid_amount', 'balance_amount (stale — recompute)',
                'due_date', 'order_date',
            ],
            'b2b_order_items': [
                'id', 'b2b_order_id', 'flavor_id', 'quantity', 'selling_price',
                'is_free_item', 'flavor_name',
            ],
            'b2b_payments': ['id', 'company_id', 'b2b_order_id', 'amount', 'payment_type', 'payment_date'],
            'flavors': ['id', 'name', 'price_per_kg', 'sale_price_per_kg', 'reorder_level_grams', 'is_active'],
            'stock': [
                'id', 'flavor_id', 'is_active_batch', 'quantity_grams',
                'reserved_quantity_grams', 'expiry_date', 'vendor_id',
            ],
            'stock_alerts': ['id', 'flavor_id', 'alert_type', 'current_quantity_grams', 'is_acknowledged'],
            'coupons': ['id', 'brand_id', 'code', 'discount_amount', 'discount_percentage', 'used_count', 'is_active'],
            'expenses': ['id', 'brand_id', 'category_id', 'title', 'amount', 'expense_date', 'payment_status'],
            'incomes': ['id', 'brand_id', 'source_type', 'order_id', 'b2b_order_id', 'amount', 'income_date'],
            'investments': ['id', 'partner_name', 'amount', 'investment_date'],
            'reviews': ['id', 'brand_id', 'user_id', 'rating', 'is_approved', 'created_at'],
            'users': ['id', 'brand_id', 'mobile', 'name', 'loyalty_points', 'created_at'],
            'marketing_sources': ['id', 'source_name', 'source_code'],
            'sources_tracking': ['id', 'source', 'referral', 'timestamp'],
        },
    }


def dashboard_overview(brand_id=None, days=30):
    """Revenue, order counts and AOV for B2C + B2B combined over a date range."""
    start, end = _date_range(days)

    b2c = _scope_brand(
        Order.objects.exclude(status__in=B2C_INACTIVE), brand_id,
    ).filter(order_date__date__gte=start, order_date__date__lte=end)
    b2c_count = b2c.count()
    b2c_revenue = _f(OrderItem.objects.filter(order__in=b2c).aggregate(r=Sum(B2C_REVENUE))['r'])

    b2b = _scope_brand(
        B2BOrder.objects.exclude(status__in=B2B_INACTIVE), brand_id,
    ).filter(order_date__date__gte=start, order_date__date__lte=end)
    b2b_agg = b2b.aggregate(rev=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY), cnt=Count('id'))
    b2b_count = b2b_agg['cnt']
    b2b_revenue = _f(b2b_agg['rev'])

    new_customers = _scope_brand(User.objects.all(), brand_id).filter(
        created_at__date__gte=start, created_at__date__lte=end,
    ).count()

    order_status = {
        r['status']: r['cnt']
        for r in _scope_brand(Order.objects.exclude(status='deleted'), brand_id)
        .filter(order_date__date__gte=start, order_date__date__lte=end)
        .values('status').annotate(cnt=Count('id'))
    }

    total_orders = b2c_count + b2b_count
    total_revenue = b2c_revenue + b2b_revenue

    return {
        'range': {'start': start.isoformat(), 'end': end.isoformat(), 'days': days},
        'brand_id': brand_id,
        'b2c': {'orders': b2c_count, 'revenue': b2c_revenue,
                'avg_order_value': round(b2c_revenue / b2c_count, 2) if b2c_count else 0.0},
        'b2b': {'orders': b2b_count, 'revenue': b2b_revenue,
                'avg_order_value': round(b2b_revenue / b2b_count, 2) if b2b_count else 0.0},
        'combined': {'orders': total_orders, 'revenue': total_revenue},
        'new_customers': new_customers,
        'b2c_order_status_breakdown': order_status,
    }


_TRUNC = {'day': TruncDate, 'week': TruncWeek, 'month': TruncMonth}


def sales_trend(brand_id=None, period='day', days=90):
    """B2C revenue/order-count bucketed by day, week, or month over a date range."""
    trunc = _TRUNC.get(period, TruncDate)
    start, end = _date_range(days)

    active = _scope_brand(
        Order.objects.exclude(status__in=B2C_INACTIVE), brand_id,
    ).filter(order_date__date__gte=start, order_date__date__lte=end)

    orders_by_bucket = {
        r['b']: r['cnt']
        for r in active.annotate(b=trunc('order_date')).values('b').annotate(cnt=Count('id', distinct=True))
    }
    revenue_by_bucket = {
        r['b']: _f(r['rev'])
        for r in OrderItem.objects.filter(order__in=active)
        .annotate(b=trunc('order__order_date')).values('b').annotate(rev=Sum(B2C_REVENUE))
    }
    buckets = sorted(set(orders_by_bucket) | set(revenue_by_bucket))
    return {
        'brand_id': brand_id, 'period': period,
        'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'points': [
            {
                'bucket': b.isoformat() if hasattr(b, 'isoformat') else str(b),
                'orders': orders_by_bucket.get(b, 0),
                'revenue': revenue_by_bucket.get(b, 0.0),
            }
            for b in buckets
        ],
    }


def top_products(brand_id=None, days=90, limit=10):
    """Best-selling flavors by revenue across B2C + B2B, over a date range."""
    start, end = _date_range(days)
    limit = max(1, min(int(limit or 10), 50))

    b2c_active = _scope_brand(
        Order.objects.exclude(status__in=B2C_INACTIVE), brand_id,
    ).filter(order_date__date__gte=start, order_date__date__lte=end)
    b2c_rows = {
        r['flavor_name']: r
        for r in OrderItem.objects.filter(order__in=b2c_active).values('flavor_name').annotate(
            grams=Coalesce(Sum('quantity'), Value(0)),
            revenue=Coalesce(Sum(B2C_REVENUE), Value(0.0), output_field=FloatField()),
        )
    }

    b2b_active = _scope_brand(
        B2BOrder.objects.exclude(status__in=B2B_INACTIVE), brand_id,
    ).filter(order_date__date__gte=start, order_date__date__lte=end)
    b2b_line_revenue = ExpressionWrapper(F('quantity') * F('selling_price'), output_field=MONEY)
    b2b_rows = {
        r['flavor_name']: r
        for r in B2BOrderItem.objects.filter(b2b_order__in=b2b_active, is_free_item=False)
        .values('flavor_name').annotate(
            grams=Coalesce(Sum('total_weight_grams'), Value(0)),
            revenue=Coalesce(Sum(b2b_line_revenue), Value(0), output_field=MONEY),
        )
    }

    names = set(b2c_rows) | set(b2b_rows)
    merged = []
    for name in names:
        b2c = b2c_rows.get(name, {'grams': 0, 'revenue': 0})
        b2b = b2b_rows.get(name, {'grams': 0, 'revenue': 0})
        merged.append({
            'flavor_name': name,
            'b2c_grams': int(b2c['grams'] or 0), 'b2c_revenue': _f(b2c['revenue']),
            'b2b_grams': int(b2b['grams'] or 0), 'b2b_revenue': _f(b2b['revenue']),
            'total_revenue': _f(b2c['revenue']) + _f(b2b['revenue']),
        })
    merged.sort(key=lambda r: -r['total_revenue'])
    return {
        'brand_id': brand_id, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'products': merged[:limit],
    }


def stock_status(brand_id=None):
    """Active-batch stock levels and open alerts per flavor (Flavor/Stock are
    global, not brand-scoped, so brand_id is accepted for a consistent
    signature but has no effect here)."""
    flavors = Flavor.objects.filter(is_active=True).prefetch_related('stock_batches')
    rows = []
    for f in flavors:
        available = available_grams(f)
        rows.append({
            'flavor_id': f.id, 'flavor_name': f.name,
            'available_grams': available,
            'available_kg': round(available / 1000, 2),
            'reorder_level_grams': f.reorder_level_grams,
            'status': (
                'out_of_stock' if available <= 0
                else 'low_stock' if available <= f.reorder_level_grams
                else 'ok'
            ),
        })
    rows.sort(key=lambda r: r['available_grams'])

    alerts = (
        StockAlert.objects.filter(is_acknowledged=False)
        .select_related('flavor').order_by('-created_at')[:50]
    )
    expiring = (
        Stock.objects.filter(quantity_grams__gt=0, expiry_date__isnull=False)
        .filter(expiry_date__lte=timezone.localdate() + timedelta(days=30))
        .select_related('flavor').order_by('expiry_date')[:50]
    )
    return {
        'flavors': rows,
        'open_alerts': [
            {
                'id': a.id, 'flavor_name': a.flavor.name, 'alert_type': a.alert_type,
                'current_quantity_grams': a.current_quantity_grams, 'created_at': a.created_at.isoformat(),
            }
            for a in alerts
        ],
        'expiring_within_30_days': [
            {
                'stock_id': s.id, 'flavor_name': s.flavor.name, 'batch_number': s.batch_number,
                'quantity_grams': s.quantity_grams, 'expiry_date': s.expiry_date.isoformat(),
            }
            for s in expiring
        ],
    }


def b2b_pipeline_summary(brand_id=None):
    """Company counts/value by pipeline stage, plus overall conversion rate."""
    companies = _scope_brand(B2BCompany.objects.filter(is_active=True), brand_id)
    by_stage = {r['stage']: r['cnt'] for r in companies.values('stage').annotate(cnt=Count('id'))}
    total = companies.count()
    converted = by_stage.get('converted', 0)

    revenue_by_company = (
        companies.filter(stage='converted')
        .annotate(revenue=Coalesce(
            Sum('orders__total_amount', filter=~Q(orders__status='cancelled')),
            Value(0), output_field=MONEY,
        ))
    )
    total_converted_revenue = _f(revenue_by_company.aggregate(r=Sum('revenue'))['r'])

    return {
        'brand_id': brand_id,
        'total_companies': total,
        'by_stage': {
            stage: by_stage.get(stage, 0)
            for stage in ['lead', 'contacted', 'negotiation', 'converted', 'lost']
        },
        'conversion_rate_pct': round(converted / total * 100, 1) if total else 0.0,
        'total_converted_lifetime_revenue': total_converted_revenue,
    }


def b2b_outstanding(brand_id=None, limit=20):
    """Outstanding / overdue / advance-credit, aggregated and per top company.
    Always total_amount - paid_amount (never the stored balance_amount)."""
    limit = max(1, min(int(limit or 20), 100))
    today = timezone.localdate()

    orders = _scope_brand(
        B2BOrder.objects.exclude(status='cancelled'), brand_id,
    ).annotate(balance=BALANCE)
    agg = orders.aggregate(
        outstanding=Coalesce(Sum('balance', filter=Q(balance__gt=0)), Value(0), output_field=MONEY),
        overdue=Coalesce(Sum('balance', filter=Q(balance__gt=0, due_date__lt=today)), Value(0), output_field=MONEY),
        advance=Coalesce(
            Sum(Case(When(balance__lt=0, then=-F('balance')), output_field=MONEY)),
            Value(0), output_field=MONEY,
        ),
    )
    overdue_count = orders.filter(balance__gt=0, due_date__lt=today).count()

    company_balance = F('orders__total_amount') - F('orders__paid_amount')
    companies = (
        _scope_brand(B2BCompany.objects.filter(is_active=True), brand_id)
        .annotate(
            outstanding=Coalesce(
                Sum(company_balance, filter=~Q(orders__status='cancelled') & Q(
                    orders__total_amount__gt=F('orders__paid_amount'),
                )),
                Value(0), output_field=MONEY,
            ),
        )
        .filter(outstanding__gt=0)
        .order_by('-outstanding')[:limit]
    )

    return {
        'brand_id': brand_id, 'as_of': today.isoformat(),
        'total_outstanding': _f(agg['outstanding']),
        'total_overdue': _f(agg['overdue']),
        'overdue_order_count': overdue_count,
        'total_advance_credit': _f(agg['advance']),
        'top_companies_by_outstanding': [
            {'id': c.id, 'company_name': c.company_name, 'outstanding': _f(c.outstanding)}
            for c in companies
        ],
    }


def b2b_report(brand_id=None, days=30):
    """Full B2B analysis for a date range (pipeline, flavor trends, top/at-risk
    companies, financial health, flagged issues) — reuses the same report the
    B2B dashboard's 'Generate Report' button builds
    (orders.reports_b2b.build_b2b_report). Needs exactly one brand; falls
    back to the first active Brand if brand_id is omitted."""
    bid = brand_id if brand_id is not None else _default_brand_id()
    if bid is None:
        return {'error': 'No active brand found.'}
    start, end = _date_range(days)
    report = reports_b2b.build_b2b_report(bid, start, end)
    report['start'] = report['start'].isoformat()
    report['end'] = report['end'].isoformat()
    report['today'] = report['today'].isoformat()
    prev = report['previous']
    prev['prev_start'] = prev['prev_start'].isoformat()
    prev['prev_end'] = prev['prev_end'].isoformat()
    for at_risk in report['companies']['at_risk']:
        if at_risk['last_order_date']:
            at_risk['last_order_date'] = at_risk['last_order_date'].isoformat()
    report['brand_id'] = bid
    return report


def customer_insights(brand_id=None, days=30, limit=10):
    """New vs returning customers, loyalty points outstanding, top spenders."""
    limit = max(1, min(int(limit or 10), 50))
    start, end = _date_range(days)

    customers = _scope_brand(User.objects.all(), brand_id)
    new_customers = customers.filter(created_at__date__gte=start, created_at__date__lte=end).count()
    total_customers = customers.count()

    active_orders = Order.objects.exclude(status__in=B2C_INACTIVE).filter(
        order_date__date__gte=start, order_date__date__lte=end,
    )
    returning = active_orders.values('user_id').annotate(cnt=Count('id')).filter(cnt__gt=1, user_id__isnull=False).count()

    loyalty_pending = customers.aggregate(total=Coalesce(Sum('loyalty_points'), Value(0)))['total']

    customer_line_revenue = ExpressionWrapper(
        F('orders__items__quantity') * F('orders__items__sale_price_per_kg') / 1000.0,
        output_field=FloatField(),
    )
    top_spenders = (
        customers.annotate(
            spend=Coalesce(
                Sum(customer_line_revenue, filter=Q(orders__status__in=['delivered', 'shipped', 'processing', 'confirmed'])),
                Value(0.0), output_field=FloatField(),
            ),
        )
        .order_by('-spend')[:limit]
    )
    return {
        'brand_id': brand_id, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'total_customers': total_customers,
        'new_customers_in_range': new_customers,
        'returning_customers_in_range': returning,
        'total_loyalty_points_held': int(loyalty_pending or 0),
        'top_spenders': [
            {'id': c.id, 'name': c.name or c.mobile, 'mobile': c.mobile, 'lifetime_spend': _f(c.spend)}
            for c in top_spenders if _f(c.spend) > 0
        ],
    }


def coupon_performance(brand_id=None, days=90):
    """Usage count, discount given, and revenue on orders that used each coupon."""
    start, end = _date_range(days)
    coupons = _scope_brand(Coupon.objects.all(), brand_id)

    orders = Order.objects.exclude(status__in=B2C_INACTIVE).filter(
        order_date__date__gte=start, order_date__date__lte=end, coupon_id__isnull=False,
    )
    by_coupon = {
        r['coupon_id']: r
        for r in orders.values('coupon_id').annotate(
            uses=Count('id'),
            discount=Coalesce(Sum('coupon_discount'), Value(0.0), output_field=FloatField()),
        )
    }
    revenue_by_coupon = {
        r['order__coupon_id']: _f(r['rev'])
        for r in OrderItem.objects.filter(order__in=orders).values('order__coupon_id').annotate(rev=Sum(B2C_REVENUE))
    }

    rows = []
    for c in coupons:
        stat = by_coupon.get(c.id)
        if not stat:
            continue
        rows.append({
            'code': c.code, 'is_active': c.is_active,
            'uses_in_range': stat['uses'], 'lifetime_used_count': c.used_count,
            'discount_given_in_range': _f(stat['discount']),
            'revenue_influenced_in_range': revenue_by_coupon.get(c.id, 0.0),
        })
    rows.sort(key=lambda r: -r['uses_in_range'])
    return {
        'brand_id': brand_id, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'coupons': rows,
    }


def finance_summary(brand_id=None, days=30):
    """Expenses vs Income (booked when an order is fully paid), grouped by
    category/date range — a profitability view, not just cost tracking."""
    start, end = _date_range(days)

    expenses = _scope_brand(Expense.objects.all(), brand_id).filter(
        expense_date__gte=start, expense_date__lte=end,
    )
    by_category = list(
        expenses.values('category__name').annotate(
            total=Coalesce(Sum('amount'), Value(0), output_field=MONEY),
        ).order_by('-total'),
    )
    total_expenses = _f(expenses.aggregate(t=Sum('amount'))['t'])

    income = _scope_brand(Income.objects.all(), brand_id).filter(
        income_date__gte=start, income_date__lte=end,
    )
    total_income = _f(income.aggregate(t=Sum('amount'))['t'])
    income_by_source = {
        r['source_type']: _f(r['t'])
        for r in income.values('source_type').annotate(t=Sum('amount'))
    }

    return {
        'brand_id': brand_id, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'total_income': total_income,
        'income_by_source': income_by_source,
        'total_expenses': total_expenses,
        'expenses_by_category': [
            {'category': r['category__name'], 'total': _f(r['total'])} for r in by_category
        ],
        'net': total_income - total_expenses,
    }


def marketing_source_performance(days=90):
    """Traffic/conversion by marketing source (QR codes, campaign codes)."""
    start, end = _date_range(days)
    hits = (
        SourceTracking.objects.filter(timestamp__date__gte=start, timestamp__date__lte=end)
        .values('source').annotate(hits=Count('id')).order_by('-hits')
    )
    known_sources = {s.source_code: s.source_name for s in MarketingSource.objects.all()}
    return {
        'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'sources': [
            {
                'source_code': r['source'],
                'source_name': known_sources.get(r['source'], r['source']),
                'hits': r['hits'],
            }
            for r in hits
        ],
    }


def review_summary(brand_id=None, days=90):
    """Approved review volume/rating distribution over a date range."""
    start, end = _date_range(days)
    reviews = _scope_brand(Review.objects.all(), brand_id).filter(
        created_at__date__gte=start, created_at__date__lte=end,
    )
    approved = reviews.filter(is_approved=True)
    by_rating = {r['rating']: r['cnt'] for r in approved.values('rating').annotate(cnt=Count('id'))}
    avg_rating = approved.aggregate(a=Coalesce(Sum('rating'), Value(0)))['a']
    approved_count = approved.count()
    return {
        'brand_id': brand_id, 'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'total_submitted': reviews.count(),
        'approved': approved_count,
        'pending_approval': reviews.filter(is_approved=False).count(),
        'avg_rating': round(avg_rating / approved_count, 2) if approved_count else None,
        'rating_breakdown': {str(k): by_rating.get(k, 0) for k in range(1, 6)},
    }
