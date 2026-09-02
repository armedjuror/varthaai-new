"""
B2B business report: date-range scoped analysis behind the B2B dashboard's
"Generate Report" button (a printable page, see orders/views_b2b_dashboard.py
:b2b_report_page and templates/admin/b2b-report.html).

Self-contained — does not import from views_b2b_dashboard.py, which calls
into this module, so importing the other way would be circular. Balances are
always computed from `total_amount - paid_amount`, never the stored
`balance_amount` column, matching the rest of the B2B codebase.
"""
from datetime import timedelta

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum, Value
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from crm.models import B2BCompany
from orders.models import B2BOrder, B2BOrderItem

MONEY = DecimalField(max_digits=14, decimal_places=2)
BALANCE = F('total_amount') - F('paid_amount')

STAGE_ORDER = ['lead', 'contacted', 'negotiation', 'converted', 'lost']
STAGE_LABELS = {
    'lead': 'Lead', 'contacted': 'Contacted', 'negotiation': 'Negotiation',
    'converted': 'Converted', 'lost': 'Lost',
}
AT_RISK_CAP = 15
FLAVOR_GROWTH_MIN_VOLUME = 4  # skip flavors with too little volume to avoid noisy % swings
REP_MIN_LEADS_FOR_FLAG = 5
REP_LAG_THRESHOLD_POINTS = 15
COLLECTION_RATE_HEALTHY_PCT = 70


def _f(value):
    return float(value) if value is not None else 0.0


def _pct(numerator, denominator):
    return round(numerator / denominator * 100, 1) if denominator else 0.0


def _pct_change(curr, prev):
    if not prev:
        return 100.0 if curr > 0 else 0.0
    return round((curr - prev) / prev * 100, 1)


def build_b2b_report(brand_id, start, end):
    today = timezone.localdate()

    headline = _headline(brand_id, start, end)
    previous = _previous_period(brand_id, start, end, headline)
    pipeline = _pipeline_snapshot(brand_id, start, end)
    flavors = _flavor_insights(brand_id, start, end)
    companies = _company_insights(brand_id, start, end, today)
    financial = _financial_health(brand_id, start, end, today)
    flags = _optimization_flags(pipeline, flavors, companies, financial)

    return {
        'start': start, 'end': end, 'today': today,
        'headline': headline,
        'previous': previous,
        'pipeline': pipeline,
        'flavors': flavors,
        'companies': companies,
        'financial': financial,
        'flags': flags,
    }


def _headline(brand_id, start, end):
    leads_qs = B2BCompany.objects.filter(
        brand_id=brand_id, created_at__date__gte=start, created_at__date__lte=end,
    )
    new_leads = leads_qs.count()
    converted_of_cohort = leads_qs.filter(converted_at__isnull=False).count()

    converted_in_range = B2BCompany.objects.filter(
        brand_id=brand_id, converted_at__date__gte=start, converted_at__date__lte=end,
    ).count()

    orders_qs = (
        B2BOrder.objects.filter(brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=end)
        .exclude(status='cancelled')
    )
    order_agg = orders_qs.aggregate(
        orders=Count('id'),
        revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
        paid=Coalesce(Sum('paid_amount'), Value(0), output_field=MONEY),
    )
    orders_count = order_agg['orders']
    revenue = _f(order_agg['revenue'])
    paid = _f(order_agg['paid'])
    pending = max(revenue - paid, 0.0)
    aov = round(revenue / orders_count) if orders_count else 0

    total_packs = (
        B2BOrderItem.objects.filter(
            b2b_order__brand_id=brand_id,
            b2b_order__order_date__date__gte=start,
            b2b_order__order_date__date__lte=end,
        )
        .exclude(b2b_order__status='cancelled')
        .aggregate(p=Coalesce(Sum('quantity'), Value(0)))['p']
    )

    converted_cohort = B2BCompany.objects.filter(
        brand_id=brand_id, converted_at__date__gte=start, converted_at__date__lte=end,
    ).only('created_at', 'converted_at')
    convert_days = [(c.converted_at.date() - c.created_at.date()).days for c in converted_cohort]
    avg_convert_days = round(sum(convert_days) / len(convert_days), 1) if convert_days else None

    return {
        'new_leads': new_leads,
        'converted_of_cohort': converted_of_cohort,
        'cohort_conversion_rate': _pct(converted_of_cohort, new_leads),
        'converted_in_range': converted_in_range,
        'orders': orders_count,
        'revenue': revenue, 'paid': paid, 'pending': pending, 'aov': aov,
        'total_packs': total_packs,
        'avg_convert_days': avg_convert_days,
    }


def _previous_period(brand_id, start, end, headline):
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)

    prev_leads = B2BCompany.objects.filter(
        brand_id=brand_id, created_at__date__gte=prev_start, created_at__date__lte=prev_end,
    ).count()
    prev_converted = B2BCompany.objects.filter(
        brand_id=brand_id, converted_at__date__gte=prev_start, converted_at__date__lte=prev_end,
    ).count()

    prev_orders_qs = (
        B2BOrder.objects.filter(brand_id=brand_id, order_date__date__gte=prev_start, order_date__date__lte=prev_end)
        .exclude(status='cancelled')
    )
    prev_agg = prev_orders_qs.aggregate(
        orders=Count('id'), revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
    )
    prev_orders = prev_agg['orders']
    prev_revenue = _f(prev_agg['revenue'])

    prev_packs = (
        B2BOrderItem.objects.filter(
            b2b_order__brand_id=brand_id,
            b2b_order__order_date__date__gte=prev_start,
            b2b_order__order_date__date__lte=prev_end,
        )
        .exclude(b2b_order__status='cancelled')
        .aggregate(p=Coalesce(Sum('quantity'), Value(0)))['p']
    )

    day_rows = list(
        B2BOrder.objects.filter(brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=end)
        .exclude(status='cancelled')
        .annotate(d=TruncDate('order_date'))
        .values('d')
        .annotate(revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY), orders=Count('id')),
    )
    best_revenue_day = max(day_rows, key=lambda r: r['revenue'], default=None)
    best_orders_day = max(day_rows, key=lambda r: r['orders'], default=None)

    return {
        'range_days': span,
        'prev_start': prev_start, 'prev_end': prev_end,
        'prev_leads': prev_leads, 'prev_converted': prev_converted,
        'prev_orders': prev_orders, 'prev_revenue': prev_revenue, 'prev_packs': prev_packs,
        'leads_change': _pct_change(headline['new_leads'], prev_leads),
        'converted_change': _pct_change(headline['converted_in_range'], prev_converted),
        'orders_change': _pct_change(headline['orders'], prev_orders),
        'revenue_change': _pct_change(headline['revenue'], prev_revenue),
        'packs_change': _pct_change(headline['total_packs'], prev_packs),
        'best_revenue_day': best_revenue_day,
        'best_orders_day': best_orders_day,
    }


def _pipeline_snapshot(brand_id, start, end):
    cohort = B2BCompany.objects.filter(brand_id=brand_id, created_at__date__gte=start, created_at__date__lte=end)
    total = cohort.count()
    counts = dict.fromkeys(STAGE_ORDER, 0)
    for row in cohort.values('stage').annotate(cnt=Count('id')):
        if row['stage'] in counts:
            counts[row['stage']] = row['cnt']

    stages = [
        {'key': s, 'label': STAGE_LABELS[s], 'count': counts[s], 'pct': _pct(counts[s], total)}
        for s in STAGE_ORDER
    ]
    in_progress = counts['lead'] + counts['contacted'] + counts['negotiation']

    return {
        'cohort_size': total,
        'stages': stages,
        'conversion_rate': _pct(counts['converted'], total),
        'loss_rate': _pct(counts['lost'], total),
        'in_progress_count': in_progress,
        'in_progress_rate': _pct(in_progress, total),
        'lead_stuck_pct': _pct(counts['lead'], total),
    }


def _flavor_insights(brand_id, start, end):
    items_qs = (
        B2BOrderItem.objects.filter(
            b2b_order__brand_id=brand_id,
            b2b_order__order_date__date__gte=start,
            b2b_order__order_date__date__lte=end,
        )
        .exclude(b2b_order__status='cancelled')
    )

    by_flavor = (
        items_qs.values('flavor_name')
        .annotate(
            packs=Coalesce(Sum('quantity'), Value(0)),
            revenue=Coalesce(
                Sum(ExpressionWrapper(F('quantity') * F('selling_price'), output_field=MONEY)),
                Value(0), output_field=MONEY,
            ),
        )
        .order_by('-packs')
    )
    flavors = [
        {'flavor_name': r['flavor_name'], 'packs': r['packs'], 'revenue': _f(r['revenue'])}
        for r in by_flavor
    ]
    total_packs = sum(f['packs'] for f in flavors)
    for f in flavors:
        f['pct'] = _pct(f['packs'], total_packs)

    span_days = (end - start).days + 1
    mid = start + timedelta(days=span_days // 2)
    first_map = {
        r['flavor_name']: r['packs']
        for r in items_qs.filter(b2b_order__order_date__date__lt=mid)
        .values('flavor_name').annotate(packs=Coalesce(Sum('quantity'), Value(0)))
    }
    second_map = {
        r['flavor_name']: r['packs']
        for r in items_qs.filter(b2b_order__order_date__date__gte=mid)
        .values('flavor_name').annotate(packs=Coalesce(Sum('quantity'), Value(0)))
    }
    growth = []
    for name in set(first_map) | set(second_map):
        f1, f2 = first_map.get(name, 0), second_map.get(name, 0)
        if f1 + f2 < FLAVOR_GROWTH_MIN_VOLUME:
            continue
        growth.append({'flavor_name': name, 'first_half': f1, 'second_half': f2, 'change_pct': _pct_change(f2, f1)})
    growth.sort(key=lambda g: g['change_pct'])
    biggest_decline = growth[0] if growth and growth[0]['change_pct'] < 0 else None
    biggest_growth = growth[-1] if growth and growth[-1]['change_pct'] > 0 else None

    return {
        'flavors': flavors,
        'total_packs': total_packs,
        'biggest_decline': biggest_decline,
        'biggest_growth': biggest_growth,
    }


def _company_insights(brand_id, start, end, today):
    in_range_order = Q(orders__order_date__date__gte=start, orders__order_date__date__lte=end) & ~Q(orders__status='cancelled')

    top_companies_qs = (
        B2BCompany.objects.filter(brand_id=brand_id, is_active=True)
        .annotate(
            revenue=Coalesce(Sum('orders__total_amount', filter=in_range_order), Value(0), output_field=MONEY),
            order_count=Count('orders', filter=in_range_order),
        )
        .filter(Q(revenue__gt=0) | Q(order_count__gt=0))
        .order_by('-revenue')[:10]
    )
    top_companies = [
        {'id': c.id, 'company_name': c.company_name, 'revenue': _f(c.revenue), 'order_count': c.order_count}
        for c in top_companies_qs
    ]

    at_risk_qs = (
        B2BCompany.objects.filter(brand_id=brand_id, is_active=True, stage='converted')
        .exclude(in_range_order)
        .annotate(last_order_date=Max('orders__order_date'))
        .order_by(F('last_order_date').asc(nulls_first=True))
    )
    at_risk_total = at_risk_qs.count()
    at_risk = [
        {
            'id': c.id, 'company_name': c.company_name,
            'last_order_date': c.last_order_date,
            'days_since': (today - c.last_order_date.date()).days if c.last_order_date else None,
        }
        for c in at_risk_qs[:AT_RISK_CAP]
    ]

    source_rows = (
        B2BCompany.objects.filter(brand_id=brand_id, created_at__date__gte=start, created_at__date__lte=end)
        .values('source')
        .annotate(leads=Count('id'), converted=Count('id', filter=Q(converted_at__isnull=False)))
        .order_by('-leads')
    )
    sources = [
        {
            'source': (r['source'] or '').strip() or 'Unknown',
            'leads': r['leads'], 'converted': r['converted'],
            'conversion_rate': _pct(r['converted'], r['leads']),
        }
        for r in source_rows
    ]

    rep_order_rows = (
        B2BOrder.objects.filter(brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=end)
        .exclude(status='cancelled')
        .values('company__assigned_to_id')
        .annotate(revenue=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY))
    )
    rep_revenue_map = {r['company__assigned_to_id']: _f(r['revenue']) for r in rep_order_rows}

    rep_rows = (
        B2BCompany.objects.filter(brand_id=brand_id, created_at__date__gte=start, created_at__date__lte=end)
        .values('assigned_to_id', 'assigned_to__name', 'assigned_to__username')
        .annotate(leads=Count('id'), converted=Count('id', filter=Q(converted_at__isnull=False)))
        .order_by('-leads')
    )
    reps = []
    for r in rep_rows:
        aid = r['assigned_to_id']
        rep_name = 'Unassigned' if aid is None else (r['assigned_to__name'] or r['assigned_to__username'])
        reps.append({
            'rep': rep_name, 'leads': r['leads'], 'converted': r['converted'],
            'conversion_rate': _pct(r['converted'], r['leads']),
            'order_revenue': rep_revenue_map.get(aid, 0.0),
        })

    return {
        'top_companies': top_companies,
        'at_risk': at_risk,
        'at_risk_total': at_risk_total,
        'at_risk_truncated': at_risk_total > AT_RISK_CAP,
        'sources': sources,
        'reps': reps,
    }


def _financial_health(brand_id, start, end, today):
    orders_qs = (
        B2BOrder.objects.filter(brand_id=brand_id, order_date__date__gte=start, order_date__date__lte=end)
        .exclude(status='cancelled')
        .annotate(balance=BALANCE)
    )
    agg = orders_qs.aggregate(
        billed=Coalesce(Sum('total_amount'), Value(0), output_field=MONEY),
        paid=Coalesce(Sum('paid_amount'), Value(0), output_field=MONEY),
        outstanding=Coalesce(Sum('balance', filter=Q(balance__gt=0)), Value(0), output_field=MONEY),
        overdue=Coalesce(Sum('balance', filter=Q(balance__gt=0, due_date__lt=today)), Value(0), output_field=MONEY),
    )
    billed = _f(agg['billed'])
    paid = _f(agg['paid'])
    overdue_count = orders_qs.filter(balance__gt=0, due_date__lt=today).count()

    return {
        'billed': billed, 'paid': paid,
        'outstanding': _f(agg['outstanding']), 'overdue': _f(agg['overdue']),
        'overdue_count': overdue_count,
        'collection_rate': _pct(paid, billed),
    }


def _optimization_flags(pipeline, flavors, companies, financial):
    flags = []

    if pipeline['cohort_size'] >= 5 and pipeline['lead_stuck_pct'] >= 50:
        flags.append({
            'level': 'warning',
            'message': (
                f"{pipeline['lead_stuck_pct']}% of leads created this period are still stuck at "
                "'Lead' stage with no follow-up progress — prioritize outreach."
            ),
        })

    decline = flavors['biggest_decline']
    if decline and decline['change_pct'] <= -20:
        flags.append({
            'level': 'warning',
            'message': (
                f"{decline['flavor_name']} pack sales dropped {abs(decline['change_pct'])}% in the second half "
                f"of this period vs the first ({decline['first_half']} → {decline['second_half']} packs)."
            ),
        })

    growth = flavors['biggest_growth']
    if growth and growth['change_pct'] >= 20:
        flags.append({
            'level': 'info',
            'message': (
                f"{growth['flavor_name']} pack sales grew {growth['change_pct']}% in the second half of this "
                f"period vs the first ({growth['first_half']} → {growth['second_half']} packs)."
            ),
        })

    team_avg = pipeline['conversion_rate']
    for rep in companies['reps']:
        if rep['leads'] >= REP_MIN_LEADS_FOR_FLAG and rep['conversion_rate'] < team_avg - REP_LAG_THRESHOLD_POINTS:
            flags.append({
                'level': 'warning',
                'message': (
                    f"{rep['rep']}'s conversion rate ({rep['conversion_rate']}%) is well below the team average "
                    f"({team_avg}%) on {rep['leads']} leads this period."
                ),
            })

    if financial['overdue_count'] > 0:
        flags.append({
            'level': 'warning',
            'message': (
                f"{financial['overdue_count']} overdue payment(s) totaling "
                f"₹{financial['overdue']:,.0f} need collection follow-up."
            ),
        })

    if companies['at_risk_total'] > 0:
        flags.append({
            'level': 'warning',
            'message': (
                f"{companies['at_risk_total']} converted customer(s) placed no orders in this period — "
                "consider re-engagement."
            ),
        })

    if financial['billed'] > 0 and financial['collection_rate'] < COLLECTION_RATE_HEALTHY_PCT:
        flags.append({
            'level': 'warning',
            'message': (
                f"Collection rate is {financial['collection_rate']}% for orders billed this period — "
                f"below the {COLLECTION_RATE_HEALTHY_PCT}% healthy threshold."
            ),
        })

    if not flags:
        flags.append({
            'level': 'positive',
            'message': 'No issues detected in this period — pipeline, payments and flavor sales all look healthy.',
        })

    return flags
