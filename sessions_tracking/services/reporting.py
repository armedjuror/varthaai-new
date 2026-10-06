"""
The single source of truth for "what happened for user U on day D" and for
period aggregates built from it. The Telegram report and every dashboard
view call these functions — metric logic must never be duplicated
elsewhere (see module docstring in sessions_tracking/models.py).

B2B order -> employee attribution: `orders.B2BOrder.created_by` (the admin
user who created/confirmed the order). `orders.B2BPayment.created_by` for
collections. These are the only user-attribution fields available on those
models today (see Step 0 exploration) — stated here as the load-bearing
assumption for every revenue-side metric below.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from orders.models import B2BOrder, B2BOrderItem, B2BPayment
from sessions_tracking.models import PackingItem, Session, SessionEvent, Visit
from sessions_tracking.services.leave import day_status, effective_weekly_off_weekday, leave_balance
from sessions_tracking.timeutil import ist_day_bounds, ist_now, to_ist

ZERO = Decimal('0')


def _session_time_breakdown(session, now):
    """Active/break minutes from SessionEvent (source of truth), plus total
    wall-clock span. `now` is the bound to use if the session is still open."""
    end_bound = session.ended_at or now
    total_minutes = max((end_bound - session.started_at).total_seconds() / 60.0, 0.0)

    events = list(session.events.order_by('at'))
    break_minutes = 0.0
    break_minutes_by_reason = defaultdict(float)
    open_break_start = None
    open_break_reason = None
    for ev in events:
        if ev.event == SessionEvent.Event.BREAK:
            open_break_start = ev.at
            open_break_reason = ev.reason
        elif ev.event == SessionEvent.Event.RESUME and open_break_start is not None:
            dur = (ev.at - open_break_start).total_seconds() / 60.0
            break_minutes += dur
            if open_break_reason:
                break_minutes_by_reason[open_break_reason] += dur
            open_break_start = None
            open_break_reason = None
    if open_break_start is not None:
        # Defensive only — end_session() always resumes before ending.
        dur = (end_bound - open_break_start).total_seconds() / 60.0
        break_minutes += dur
        if open_break_reason:
            break_minutes_by_reason[open_break_reason] += dur

    active_minutes = max(total_minutes - break_minutes, 0.0)
    return {
        'total_minutes': round(total_minutes),
        'active_minutes': round(active_minutes),
        'break_minutes': round(break_minutes),
        'break_minutes_lunch': round(break_minutes_by_reason.get('lunch_break', 0.0)),
        'break_minutes_evening': round(break_minutes_by_reason.get('evening_break', 0.0)),
        'events': [{'event': e.event, 'at': e.at, 'reason': e.reason} for e in events],
    }


def _day_session(user, session_type, start, end):
    return (
        Session.objects.filter(user=user, type=session_type, started_at__gte=start, started_at__lt=end)
        .order_by('started_at')
        .first()
    )


def _is_retargeted(visit, date):
    company = visit.company
    if company.orders.exists():
        return False
    return to_ist(company.created_at).date() < date


def build_daily_report(user, date):
    """Everything known about `user` on IST calendar date `date`."""
    start, end = ist_day_bounds(date)
    now = ist_now()
    status = day_status(user, date)

    sales = _day_session(user, Session.Type.SALES, start, end)
    packing = _day_session(user, Session.Type.PACKING, start, end)

    sales_block = None
    reached_home_time = None
    reached_home_recorded = False
    start_time = None
    if sales:
        times = _session_time_breakdown(sales, now)
        start_time = sales.started_at
        reached_home_recorded = sales.ended_at is not None and not sales.auto_closed
        reached_home_time = sales.ended_at if reached_home_recorded else None
        sales_block = {
            'id': sales.id,
            'started_at': sales.started_at,
            'ended_at': sales.ended_at,
            'status': sales.status,
            'auto_closed': sales.auto_closed,
            'end_edited': sales.end_edited,
            'end_edit_note': sales.end_edit_note,
            **times,
        }

    packing_block = None
    if packing:
        times = _session_time_breakdown(packing, now)
        items = list(
            PackingItem.objects.filter(session=packing).select_related('flavor').order_by('flavor__name'),
        )
        total_packs = sum(i.packs for i in items)
        packing_block = {
            'id': packing.id,
            'started_at': packing.started_at,
            'ended_at': packing.ended_at,
            'auto_closed': packing.auto_closed,
            'end_edited': packing.end_edited,
            'end_edit_note': packing.end_edit_note,
            'items': [{'flavor_id': i.flavor_id, 'flavor_name': i.flavor.name, 'packs': i.packs} for i in items],
            'total_packs': total_packs,
            'minutes_per_pack': (times['active_minutes'] / total_packs) if total_packs else None,
            'flagged_no_items': packing.auto_closed and not items,
            **times,
        }

    visits_qs = list(
        Visit.objects.filter(user=user, visited_at__gte=start, visited_at__lt=end)
        .select_related('company')
        .order_by('visited_at'),
    )
    last_meeting_time = visits_qs[-1].visited_at if visits_qs else None
    new_leads = sum(1 for v in visits_qs if v.purpose == Visit.Purpose.NEW_LEAD)
    retargeted_leads = sum(1 for v in visits_qs if v.purpose == Visit.Purpose.RETARGET and _is_retargeted(v, date))

    delivered_orders = list(
        B2BOrder.objects.filter(
            created_by=user, status=B2BOrder.Status.DELIVERED,
            delivered_at__gte=start, delivered_at__lt=end,
        ),
    )
    orders_count = len(delivered_orders)
    first_delivery_time = min((o.delivered_at for o in delivered_orders), default=None)
    packs = B2BOrderItem.objects.filter(
        b2b_order__in=[o.id for o in delivered_orders],
    ).aggregate(total=Sum('quantity'))['total'] or 0

    collection = B2BPayment.objects.filter(created_by=user, payment_date=date).aggregate(
        total=Sum('amount'),
    )['total'] or ZERO

    flags = {
        'auto_closed_sales': bool(sales and sales.auto_closed),
        'auto_closed_packing': bool(packing and packing.auto_closed),
        'end_edited_sales': bool(sales and sales.end_edited),
        'end_edited_packing': bool(packing and packing.end_edited),
        'packing_no_items': bool(packing_block and packing_block['flagged_no_items']),
        'no_session_on_working_day': status == 'working' and not sales and not packing,
    }

    return {
        'user_id': user.id,
        'date': date,
        'day_status': status,
        'leave_balance': leave_balance(user, date.year),
        'weekly_off_weekday': effective_weekly_off_weekday(user, date),
        'sales_session': sales_block,
        'packing_session': packing_block,
        'start_time': start_time,
        'first_delivery_time': first_delivery_time,
        'last_meeting_time': last_meeting_time,
        'reached_home_time': reached_home_time,
        'reached_home_recorded': reached_home_recorded,
        'packs': packs,
        'orders': orders_count,
        'collection': collection,
        'visits_count': len(visits_qs),
        'visits': [
            {
                'visited_at': v.visited_at, 'company_id': v.company_id,
                'company_name': v.company.company_name, 'purpose': v.purpose,
                'outcome': v.outcome, 'notes': v.notes,
            }
            for v in visits_qs
        ],
        'new_leads': new_leads,
        'retargeted_leads': retargeted_leads,
        'flags': flags,
        'has_any_session': bool(sales or packing),
    }


def _avg(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _time_of_day_minutes(dt):
    if dt is None:
        return None
    ist = to_ist(dt)
    return ist.hour * 60 + ist.minute + ist.second / 60.0


def build_period_report(user, start_date, end_date):
    """Day-by-day report for `user` over [start_date, end_date] (inclusive,
    IST calendar dates), plus totals/averages. Averages are computed only
    over days that actually have the relevant data — never over days with
    no session."""
    days = []
    d = start_date
    while d <= end_date:
        days.append(build_daily_report(user, d))
        d += timedelta(days=1)

    off_days = sum(1 for r in days if r['day_status'] == 'weekly_off')
    leave_days = sum(1 for r in days if r['day_status'] == 'leave')
    working_status_days = [r for r in days if r['day_status'] == 'working']
    days_worked = sum(1 for r in working_status_days if r['has_any_session'])
    no_session_days = len(working_status_days) - days_worked

    worked_rows = [r for r in days if r['has_any_session']]

    packing_flavor_totals = defaultdict(int)
    for r in days:
        if r['packing_session']:
            for item in r['packing_session']['items']:
                packing_flavor_totals[item['flavor_name']] += item['packs']

    totals = {
        'packs': sum(r['packs'] for r in days),
        'orders': sum(r['orders'] for r in days),
        'collection': sum((r['collection'] for r in days), ZERO),
        'visits': sum(r['visits_count'] for r in days),
        'new_leads': sum(r['new_leads'] for r in days),
        'retargeted_leads': sum(r['retargeted_leads'] for r in days),
        'packing_total_packs': sum(packing_flavor_totals.values()),
        'packing_by_flavor': dict(packing_flavor_totals),
    }

    def _combined_minutes(r, key):
        total, found = 0, False
        if r['sales_session']:
            total += r['sales_session'][key]
            found = True
        if r['packing_session']:
            total += r['packing_session'][key]
            found = True
        return total if found else None

    averages = {
        'start_time_minutes': _avg([_time_of_day_minutes(r['start_time']) for r in worked_rows]),
        'reached_home_minutes': _avg(
            [_time_of_day_minutes(r['reached_home_time']) for r in worked_rows if r['reached_home_recorded']],
        ),
        'active_minutes_per_day': _avg([_combined_minutes(r, 'active_minutes') for r in worked_rows]),
        'break_minutes_per_day': _avg([_combined_minutes(r, 'break_minutes') for r in worked_rows]),
        'packs_per_day': (totals['packs'] / days_worked) if days_worked else None,
        'orders_per_day': (totals['orders'] / days_worked) if days_worked else None,
        'collection_per_day': (totals['collection'] / days_worked) if days_worked else None,
        'visits_per_day': (totals['visits'] / days_worked) if days_worked else None,
        'new_leads_per_day': (totals['new_leads'] / days_worked) if days_worked else None,
        'retargeted_leads_per_day': (totals['retargeted_leads'] / days_worked) if days_worked else None,
    }

    # Weighted average (total active packing minutes / total packs), not a
    # naive mean of daily ratios — a weighted average doesn't let a
    # low-volume day skew the figure.
    packing_days_with_packs = [
        r['packing_session'] for r in days if r['packing_session'] and r['packing_session']['total_packs']
    ]
    total_packing_active_minutes = sum(p['active_minutes'] for p in packing_days_with_packs)
    averages['minutes_per_pack'] = (
        (total_packing_active_minutes / totals['packing_total_packs']) if totals['packing_total_packs'] else None
    )

    return {
        'user_id': user.id,
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'counts': {
            'total_days': len(days),
            'days_worked': days_worked,
            'off_days': off_days,
            'leave_days': leave_days,
            'no_session_days': no_session_days,
        },
        'totals': totals,
        'averages': averages,
    }


def build_period_report_for_users(users, start_date, end_date):
    """
    "All employees" period aggregate: per-user period reports, plus a
    merged day-by-day table (numeric metrics summed across users for each
    date; the `status_counts` column shows how many users were
    working/off/on-leave that date, since a single day_status column
    doesn't make sense once more than one user is involved).
    """
    users = list(users)
    per_user = {u.id: build_period_report(u, start_date, end_date) for u in users}

    dates = []
    d = start_date
    while d <= end_date:
        dates.append(d)
        d += timedelta(days=1)

    merged_days = []
    for idx, d in enumerate(dates):
        rows = [per_user[u.id]['days'][idx] for u in users]
        merged_days.append({
            'date': d,
            'status_counts': dict(Counter(r['day_status'] for r in rows)),
            'employees_with_session': sum(1 for r in rows if r['has_any_session']),
            'packs': sum(r['packs'] for r in rows),
            'orders': sum(r['orders'] for r in rows),
            'collection': sum((r['collection'] for r in rows), ZERO),
            'visits_count': sum(r['visits_count'] for r in rows),
            'new_leads': sum(r['new_leads'] for r in rows),
            'retargeted_leads': sum(r['retargeted_leads'] for r in rows),
        })

    counts = {
        'total_days': len(dates),
        'days_worked': sum(pr['counts']['days_worked'] for pr in per_user.values()),
        'off_days': sum(pr['counts']['off_days'] for pr in per_user.values()),
        'leave_days': sum(pr['counts']['leave_days'] for pr in per_user.values()),
        'no_session_days': sum(pr['counts']['no_session_days'] for pr in per_user.values()),
    }
    totals = {
        'packs': sum(pr['totals']['packs'] for pr in per_user.values()),
        'orders': sum(pr['totals']['orders'] for pr in per_user.values()),
        'collection': sum((pr['totals']['collection'] for pr in per_user.values()), ZERO),
        'visits': sum(pr['totals']['visits'] for pr in per_user.values()),
        'new_leads': sum(pr['totals']['new_leads'] for pr in per_user.values()),
        'retargeted_leads': sum(pr['totals']['retargeted_leads'] for pr in per_user.values()),
        'packing_total_packs': sum(pr['totals']['packing_total_packs'] for pr in per_user.values()),
    }
    denom = counts['days_worked'] or None
    averages = {
        f'{key}_per_working_day': (totals[key] / denom) if denom else None
        for key in ('packs', 'orders', 'collection', 'visits', 'new_leads', 'retargeted_leads')
    }

    return {
        'start_date': start_date,
        'end_date': end_date,
        'days': merged_days,
        'counts': counts,
        'totals': totals,
        'averages': averages,
        'per_user': per_user,
    }
