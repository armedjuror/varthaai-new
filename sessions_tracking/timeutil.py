"""
IST (Asia/Kolkata) time helpers.

The project's global TIME_ZONE is UTC (Varthaai/settings.py) and Django
stores every datetime in UTC (USE_TZ=True) — but every business rule in this
app ("day", "7:30 PM cutoff", "next Monday") is defined in IST. Every
datetime boundary in this app goes through these helpers rather than
django.utils.timezone's default-timezone helpers, which would reason in UTC.

Note: `payment_date` (orders.B2BPayment) and `Leave.date` are plain
DateFields, not datetimes — compare those directly against an IST calendar
date, never run them through ist_day_bounds().
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone as dj_timezone

IST = ZoneInfo('Asia/Kolkata')

CUTOFF_HOUR = 19
CUTOFF_MINUTE = 30


def ist_now():
    """Current time, timezone-aware, in IST."""
    return dj_timezone.now().astimezone(IST)


def to_ist(dt):
    """Convert an aware datetime to IST. Naive datetimes are assumed UTC."""
    if dj_timezone.is_naive(dt):
        dt = dj_timezone.make_aware(dt, dj_timezone.utc)
    return dt.astimezone(IST)


def ist_today():
    """Today's calendar date, as seen in IST."""
    return ist_now().date()


def ist_day_bounds(day):
    """
    Half-open [start, end) window for an IST calendar date `day`, as aware
    datetimes — safe to use directly in filters against UTC-stored
    DateTimeField columns (started_at, visited_at, delivered_at, at, ...).
    """
    start = datetime.combine(day, time.min, tzinfo=IST)
    end = start + timedelta(days=1)
    return start, end


def cutoff_at(day):
    """The 19:30 IST auto-close/report-send instant for `day`."""
    return datetime.combine(day, time(CUTOFF_HOUR, CUTOFF_MINUTE), tzinfo=IST)


def is_after_cutoff(at=None):
    """True if `at` (default: now) is at/after its own IST day's 19:30 cutoff."""
    at_ist = to_ist(at) if at is not None else ist_now()
    return at_ist.time() >= time(CUTOFF_HOUR, CUTOFF_MINUTE)


def next_monday_strictly_after(from_date):
    """
    The next Monday strictly after `from_date` — if `from_date` is itself a
    Monday, rolls to the following week (today/this week can't be swapped
    retroactively). Python weekday convention: Monday=0 .. Sunday=6.
    """
    days_ahead = (7 - from_date.weekday()) % 7 or 7
    return from_date + timedelta(days=days_ahead)
