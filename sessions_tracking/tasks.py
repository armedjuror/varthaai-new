"""
Celery tasks for the daily work-session report. `run_daily_job` always
auto-closes any still-open session first, then builds and sends the Telegram
report — in that order, so closed sessions are reflected in the numbers.
Scheduled at 19:30 IST via CELERY_BEAT_SCHEDULE (Varthaai/settings.py).
"""
import logging

from celery import shared_task

from sessions_tracking.services.autoclose import autoclose_open_sessions
from sessions_tracking.services.telegram import send_daily_report
from sessions_tracking.timeutil import ist_today

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0)
def run_daily_job(self, date_iso=None):
    from datetime import date as date_cls

    day = date_cls.fromisoformat(date_iso) if date_iso else ist_today()
    try:
        closed = autoclose_open_sessions(day)
        logger.info('Auto-closed %d session(s) for %s', len(closed), day)
    except Exception:
        logger.exception('Auto-close failed for %s — report will still attempt to send.', day)

    try:
        status, _ = send_daily_report(day)
        logger.info('Daily report for %s: %s', day, status)
    except Exception:
        logger.exception('Daily report send failed for %s', day)
