"""
Overall-performance comparisons across employees: a ranking table (raw
numbers, deliberately no composite score — see module docstring in
sessions_tracking/models.py and the dashboard's design notes), weekly trend
buckets, and visits/leads conversion. Built entirely from
services.reporting.build_period_report — no metric logic is duplicated here.
"""
from datetime import timedelta
from decimal import Decimal

from orders.models import B2BOrder
from sessions_tracking.services.reporting import build_period_report

ZERO = Decimal('0')


def build_ranking(users, start_date, end_date):
    """One row per user — raw totals plus a 'consistency' ratio
    (days actually worked ÷ expected working days, i.e. days that were
    neither a weekly off nor a leave day). No weighted score."""
    rows = []
    for u in users:
        pr = build_period_report(u, start_date, end_date)
        c = pr['counts']
        expected_working_days = c['days_worked'] + c['no_session_days']
        active_minutes_total = sum(
            (r['sales_session']['active_minutes'] if r['sales_session'] else 0)
            + (r['packing_session']['active_minutes'] if r['packing_session'] else 0)
            for r in pr['days']
        )
        rows.append({
            'user_id': u.id,
            'name': u.name or u.username,
            'is_active': u.is_active,
            'collection': pr['totals']['collection'],
            'orders': pr['totals']['orders'],
            'packs': pr['totals']['packs'],
            'visits': pr['totals']['visits'],
            'new_leads': pr['totals']['new_leads'],
            'retargeted_leads': pr['totals']['retargeted_leads'],
            'active_hours': round(active_minutes_total / 60.0, 1),
            'days_worked': c['days_worked'],
            'expected_working_days': expected_working_days,
            'consistency': (c['days_worked'] / expected_working_days) if expected_working_days else None,
            'off_days': c['off_days'],
            'leave_days': c['leave_days'],
            'no_session_days': c['no_session_days'],
        })
    return rows


def _week_buckets(start_date, end_date):
    buckets = []
    cursor = start_date - timedelta(days=start_date.weekday())  # Monday on/before start
    while cursor <= end_date:
        week_start = max(cursor, start_date)
        week_end = min(cursor + timedelta(days=6), end_date)
        buckets.append((week_start, week_end))
        cursor += timedelta(days=7)
    return buckets


def weekly_trend(users, start_date, end_date):
    """Weekly buckets of collection, visits + visit->order conversion, and
    packing minutes-per-pack, summed/weighted across `users`."""
    per_user_days = {}
    for u in users:
        pr = build_period_report(u, start_date, end_date)
        per_user_days[u.id] = {r['date']: r for r in pr['days']}

    buckets = []
    for week_start, week_end in _week_buckets(start_date, end_date):
        collection = ZERO
        visits_total = 0
        orders_from_visits = 0
        packing_minutes = 0.0
        packing_packs = 0
        d = week_start
        while d <= week_end:
            for by_date in per_user_days.values():
                r = by_date.get(d)
                if not r:
                    continue
                collection += r['collection']
                visits_total += r['visits_count']
                orders_from_visits += sum(1 for v in r['visits'] if v['outcome'] == 'order')
                if r['packing_session'] and r['packing_session']['total_packs']:
                    packing_minutes += r['packing_session']['active_minutes']
                    packing_packs += r['packing_session']['total_packs']
            d += timedelta(days=1)
        buckets.append({
            'week_start': week_start,
            'week_end': week_end,
            'collection': collection,
            'visits': visits_total,
            'conversion': (orders_from_visits / visits_total) if visits_total else None,
            'minutes_per_pack': (packing_minutes / packing_packs) if packing_packs else None,
        })
    return buckets


def conversion_summary(users, start_date, end_date):
    """
    visits_to_orders = visits with outcome=order / total visits.
    leads_to_first_order = new_lead-visited companies that have since placed
    at least one B2BOrder (any status) / total new_lead-visited companies.
    """
    visits_total = 0
    orders_from_visits = 0
    new_lead_companies = set()
    for u in users:
        pr = build_period_report(u, start_date, end_date)
        for r in pr['days']:
            visits_total += r['visits_count']
            for v in r['visits']:
                if v['outcome'] == 'order':
                    orders_from_visits += 1
                if v['purpose'] == 'new_lead':
                    new_lead_companies.add(v['company_id'])

    converted = set()
    if new_lead_companies:
        converted = set(
            B2BOrder.objects.filter(company_id__in=new_lead_companies)
            .values_list('company_id', flat=True).distinct(),
        )

    return {
        'visits_total': visits_total,
        'orders_from_visits': orders_from_visits,
        'visits_to_orders': (orders_from_visits / visits_total) if visits_total else None,
        'new_leads_total': len(new_lead_companies),
        'leads_converted': len(converted),
        'leads_to_first_order': (len(converted) / len(new_lead_companies)) if new_lead_companies else None,
    }
