"""
Session state-machine: start -> (break <-> resume)* -> end.

`SessionEvent` rows are the append-only source of truth for break/active
time — they are never edited or deleted. `Session.status`/`ended_at` are
derived state kept in lock-step with the latest event by the functions
below; nothing else should write to those fields.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from products.models import Flavor
from sessions_tracking.models import PackingItem, Session, SessionEvent
from sessions_tracking.timeutil import ist_now, is_after_cutoff


def get_open_session(user, for_update=False):
    qs = Session.objects.filter(user=user, ended_at__isnull=True)
    if for_update:
        qs = qs.select_for_update()
    return qs.first()


@transaction.atomic
def start_session(user, session_type):
    if session_type not in Session.Type.values:
        raise ValidationError('Invalid session type.')
    now = ist_now()
    if is_after_cutoff(now):
        raise ValidationError('Sessions cannot be started after 7:30 PM IST.')

    existing = get_open_session(user, for_update=True)
    if existing:
        raise ValidationError(f'End your {existing.type} session first.')

    session = Session.objects.create(user=user, type=session_type, started_at=now, status=Session.Status.ACTIVE)
    SessionEvent.objects.create(session=session, event=SessionEvent.Event.START, at=now)
    return session


@transaction.atomic
def break_session(user, reason):
    if reason not in SessionEvent.Reason.values:
        raise ValidationError('Invalid break reason — choose Lunch Break or Evening Break.')
    session = get_open_session(user, for_update=True)
    if not session:
        raise ValidationError('No open session to break.')
    if session.status != Session.Status.ACTIVE:
        raise ValidationError('Cannot start a break — session is not active.')

    now = ist_now()
    SessionEvent.objects.create(session=session, event=SessionEvent.Event.BREAK, at=now, reason=reason)
    session.status = Session.Status.ON_BREAK
    session.save(update_fields=['status'])
    return session


@transaction.atomic
def resume_session(user):
    session = get_open_session(user, for_update=True)
    if not session:
        raise ValidationError('No open session to resume.')
    if session.status != Session.Status.ON_BREAK:
        raise ValidationError('Cannot resume — session is not on break.')

    now = ist_now()
    SessionEvent.objects.create(session=session, event=SessionEvent.Event.RESUME, at=now)
    session.status = Session.Status.ACTIVE
    session.save(update_fields=['status'])
    return session


def _validate_packing_items(items):
    """items: [{flavor_id, packs}, ...]. Returns a cleaned list of (flavor_id, packs)."""
    if not items:
        raise ValidationError('Add at least one flavour with packs before ending a packing session.')
    cleaned = []
    seen_flavor_ids = set()
    for row in items:
        try:
            flavor_id = int(row.get('flavor_id'))
            packs = int(row.get('packs'))
        except (TypeError, ValueError):
            raise ValidationError('Each item needs a flavor_id and a whole number of packs.')
        if packs <= 0:
            raise ValidationError('Packs must be greater than zero for every flavour.')
        if flavor_id in seen_flavor_ids:
            raise ValidationError('Each flavour can only appear once — combine the packs into one row.')
        seen_flavor_ids.add(flavor_id)
        cleaned.append((flavor_id, packs))

    found_ids = set(Flavor.objects.filter(id__in=seen_flavor_ids).values_list('id', flat=True))
    missing = seen_flavor_ids - found_ids
    if missing:
        raise ValidationError(f'Unknown flavour id(s): {", ".join(str(i) for i in sorted(missing))}.')
    return cleaned


@transaction.atomic
def end_session(user, packing_items=None):
    session = get_open_session(user, for_update=True)
    if not session:
        raise ValidationError('No open session to end.')

    now = ist_now()
    cleaned_items = None
    if session.type == Session.Type.PACKING:
        cleaned_items = _validate_packing_items(packing_items or [])

    # Ending while on a break implicitly resumes first, at the same instant,
    # so break time (break -> resume gaps) always has a well-defined end —
    # mirrors what the 19:30 auto-close does for an open break.
    if session.status == Session.Status.ON_BREAK:
        SessionEvent.objects.create(session=session, event=SessionEvent.Event.RESUME, at=now)

    SessionEvent.objects.create(session=session, event=SessionEvent.Event.END, at=now)
    session.status = Session.Status.ENDED
    session.ended_at = now
    session.save(update_fields=['status', 'ended_at'])

    if cleaned_items is not None:
        PackingItem.objects.bulk_create([
            PackingItem(session=session, flavor_id=flavor_id, packs=packs)
            for flavor_id, packs in cleaned_items
        ])

    return session


@transaction.atomic
def edit_session_end_time(session, new_ended_at, note):
    """Shared by the employee's own-session edit and the admin correction —
    callers are responsible for ownership/permission checks before calling."""
    note = (note or '').strip()
    if not note:
        raise ValidationError('A note is required to edit the end time.')
    if session.ended_at is None:
        raise ValidationError('Session is still open — end it first.')
    if new_ended_at <= session.started_at:
        raise ValidationError('End time must be after the start time.')

    session.ended_at = new_ended_at
    session.end_edited = True
    session.end_edit_note = note
    session.save(update_fields=['ended_at', 'end_edited', 'end_edit_note'])
    return session
