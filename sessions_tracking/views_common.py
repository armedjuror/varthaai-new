"""Shared helpers for the employee and admin-dashboard view modules."""
import datetime
from datetime import date as date_cls
from functools import wraps

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.api import err
from sessions_tracking.timeutil import IST, ist_today


def catch_validation(fn):
    """Turn a django.core.exceptions.ValidationError raised by a service
    function into the standard {success:false, message} envelope."""
    @wraps(fn)
    def wrapper(self, request, *args, **kwargs):
        try:
            return fn(self, request, *args, **kwargs)
        except ValidationError as e:
            message = e.messages[0] if getattr(e, 'messages', None) else str(e)
            return err(message)
    return wrapper


def parse_iso_date(value, default=None):
    if not value:
        if default is not None:
            return default
        raise ValidationError('A date is required (YYYY-MM-DD).')
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        raise ValidationError('Invalid date — use YYYY-MM-DD.')


def resolve_date_range(request):
    """Shared preset/custom date-range resolution for period reports — used
    by both the admin Employee Performance API and the employee-self
    My Performance API."""
    preset = request.query_params.get('range')
    today = ist_today()
    if preset == 'this_week':
        start = today - datetime.timedelta(days=today.weekday())
        return start, today
    if preset == 'last_week':
        this_week_start = today - datetime.timedelta(days=today.weekday())
        start = this_week_start - datetime.timedelta(days=7)
        return start, this_week_start - datetime.timedelta(days=1)
    if preset == 'this_month':
        return today.replace(day=1), today
    if preset == 'last_month':
        first_of_this_month = today.replace(day=1)
        last_month_end = first_of_this_month - datetime.timedelta(days=1)
        return last_month_end.replace(day=1), last_month_end
    start = parse_iso_date(request.query_params.get('start'), default=today - datetime.timedelta(days=29))
    end = parse_iso_date(request.query_params.get('end'), default=today)
    if start > end:
        raise ValidationError('start must be on or before end.')
    return start, end


def parse_ist_datetime(value):
    """Parse an ISO-ish datetime string from a form. A naive value (no
    timezone offset) is assumed to be IST, since that's what a human typed —
    never silently treated as UTC."""
    if not value:
        raise ValidationError('A date/time is required.')
    dt = parse_datetime(value)
    if not dt:
        raise ValidationError('Invalid date/time — use ISO 8601 (e.g. 2026-10-07T19:45).')
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=IST)
    return dt
