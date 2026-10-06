"""
Weekly off + leave. `UserSetting.weekly_off_weekday` is only ever the
*original* default — every change is layered on top as a `WeeklyOffChange`
row, so the effective weekday on any date (past or future) is always
derivable without mutating settings in place.
"""
from django.core.exceptions import ValidationError

from sessions_tracking.models import Leave, UserSetting, WeeklyOffChange
from sessions_tracking.timeutil import next_monday_strictly_after

LEAVE_ALLOWANCE_PER_YEAR = 15


def get_or_create_settings(user):
    settings_obj, _ = UserSetting.objects.get_or_create(user=user)
    return settings_obj


def effective_weekly_off_weekday(user, date):
    """The weekday (Mon=0..Sun=6) that is this user's weekly off on `date`."""
    change = (
        WeeklyOffChange.objects.filter(user=user, effective_from__lte=date)
        .order_by('-effective_from')
        .first()
    )
    if change:
        return change.new_weekday
    return get_or_create_settings(user).weekly_off_weekday


def set_weekly_off(user, new_weekday, today):
    """Schedule a weekly-off change, effective the next Monday strictly
    after `today` — a past or current day can never be swapped after the
    fact."""
    try:
        new_weekday = int(new_weekday)
    except (TypeError, ValueError):
        raise ValidationError('Invalid weekday.')
    if not 0 <= new_weekday <= 6:
        raise ValidationError('Weekday must be 0 (Monday) through 6 (Sunday).')

    effective_from = next_monday_strictly_after(today)
    return WeeklyOffChange.objects.create(
        user=user, new_weekday=new_weekday, effective_from=effective_from,
    )


def leave_balance(user, year):
    used = Leave.objects.filter(user=user, date__year=year).count()
    return {
        'year': year,
        'allowance': LEAVE_ALLOWANCE_PER_YEAR,
        'used': used,
        'remaining': max(LEAVE_ALLOWANCE_PER_YEAR - used, 0),
    }


def add_leave(user, date, note=''):
    if Leave.objects.filter(user=user, date=date).exists():
        raise ValidationError('Leave is already recorded for that date.')
    used = Leave.objects.filter(user=user, date__year=date.year).count()
    if used >= LEAVE_ALLOWANCE_PER_YEAR:
        raise ValidationError(
            f'Leave allowance exhausted — {used} of {LEAVE_ALLOWANCE_PER_YEAR} days used this year.',
        )
    return Leave.objects.create(user=user, date=date, note=(note or '').strip())


def remove_leave(user, date):
    deleted, _ = Leave.objects.filter(user=user, date=date).delete()
    if not deleted:
        raise ValidationError('No leave recorded for that date.')


def day_status(user, date):
    """'leave' | 'weekly_off' | 'working' for `date` (an IST calendar date)."""
    if Leave.objects.filter(user=user, date=date).exists():
        return 'leave'
    if date.weekday() == effective_weekly_off_weekday(user, date):
        return 'weekly_off'
    return 'working'
