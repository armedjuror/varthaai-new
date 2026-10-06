"""
Auto-close any session still open at 19:30 IST. Always run before building
the day's report (see tasks.run_daily_job) so closed sessions are reflected.
"""
from sessions_tracking.models import Session, SessionEvent
from sessions_tracking.timeutil import cutoff_at, ist_day_bounds, ist_today


def autoclose_open_sessions(today=None):
    """
    Close every session that started on IST calendar date `today` (default:
    today) and is still open at that day's 19:30 cutoff. Scoped to sessions
    that *started* that day — a stale session left open from an earlier day
    is a separate problem this job does not silently paper over. Returns the
    list of Session ids that were closed.
    """
    today = today or ist_today()
    start, _ = ist_day_bounds(today)
    cutoff = cutoff_at(today)

    closed_ids = []
    open_sessions = Session.objects.filter(
        ended_at__isnull=True, started_at__gte=start, started_at__lt=cutoff,
    )
    for session in open_sessions:
        if session.status == Session.Status.ON_BREAK:
            SessionEvent.objects.create(session=session, event=SessionEvent.Event.RESUME, at=cutoff)
        SessionEvent.objects.create(session=session, event=SessionEvent.Event.END, at=cutoff)
        session.status = Session.Status.ENDED
        session.ended_at = cutoff
        session.auto_closed = True
        session.save(update_fields=['status', 'ended_at', 'auto_closed'])
        closed_ids.append(session.id)
    return closed_ids
