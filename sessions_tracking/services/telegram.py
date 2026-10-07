"""
Daily Telegram report — one plain-text message per employee (combined into
as few Telegram messages as the 4096-char limit allows), sent via the
Telegram Bot API. Idempotent per report date via TelegramReportLog; never
logs or hard-codes TELEGRAM_BOT_TOKEN / TELEGRAM_GROUP_CHAT_ID (reused from
the new-order notification settings — same bot, same chat).
"""
import logging

import requests
from django.conf import settings

from accounts.models import AdminUser
from sessions_tracking.models import TelegramReportLog
from sessions_tracking.permissions import is_field_employee
from sessions_tracking.services.reporting import build_daily_report
from sessions_tracking.timeutil import to_ist

log = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096


def _fmt_time(dt):
    return to_ist(dt).strftime('%H:%M') if dt else '—'


def _fmt_minutes(m):
    if m is None:
        return '—'
    h, mm = divmod(int(round(m)), 60)
    return f'{h}h {mm}m'


def format_employee_report(user, report):
    name = user.name or user.username
    lines = [f'=== {name} — {report["date"].strftime("%d %b %Y")} ===']
    status = report['day_status']
    if status == 'leave':
        lb = report['leave_balance']
        lines.append(f'Leave ({lb["used"]} of {lb["allowance"]} used)')
        return '\n'.join(lines)
    if status == 'weekly_off':
        lines.append('Off (weekly off)')
        return '\n'.join(lines)

    if not report['has_any_session']:
        lines.append('[!] No session started today.')
        return '\n'.join(lines)

    sales = report['sales_session']
    if sales:
        if sales['area']:
            lines.append(f'Area: {sales["area"]}')
        lines.append(f'Start: {_fmt_time(report["start_time"])}')
        lines.append(f'First delivery: {_fmt_time(report["first_delivery_time"])}')
        lines.append(f'Last meeting: {_fmt_time(report["last_meeting_time"])}')
        if report['reached_home_recorded']:
            lines.append(f'Reached home: {_fmt_time(report["reached_home_time"])}')
        else:
            lines.append('Reached home: not recorded (auto-closed)')
        lines.append(
            f'Total {_fmt_minutes(sales["total_minutes"])} | '
            f'Active {_fmt_minutes(sales["active_minutes"])} | '
            f'Break {_fmt_minutes(sales["break_minutes"])} '
            f'(lunch {_fmt_minutes(sales["break_minutes_lunch"])}, '
            f'evening {_fmt_minutes(sales["break_minutes_evening"])})',
        )
        if sales['auto_closed']:
            lines.append('[!] Sales session auto-closed at 19:30.')
        if sales['end_edited']:
            lines.append(f'[edited] End time note: {sales["end_edit_note"]}')

    lines.append(
        f'Packs: {report["packs"]} | Orders: {report["orders"]} | '
        f'Collection: Rs.{report["collection"]:,.0f} | Visits: {report["visits_count"]} | '
        f'New leads: {report["new_leads"]} | Retargeted: {report["retargeted_leads"]}',
    )

    packing = report['packing_session']
    if packing:
        per_flavor = ', '.join(f'{i["flavor_name"]}: {i["packs"]}' for i in packing['items']) or '—'
        mpp = f'{packing["minutes_per_pack"]:.1f}' if packing['minutes_per_pack'] else '—'
        lines.append(f'Packing — {per_flavor} (total {packing["total_packs"]}, {mpp} min/pack)')
        if packing['auto_closed']:
            lines.append('[!] Packing session auto-closed at 19:30.')
        if packing['flagged_no_items']:
            lines.append('[!] Auto-closed with no packs logged.')
        if packing['end_edited']:
            lines.append(f'[edited] End time note: {packing["end_edit_note"]}')

    if not report['has_any_session'] and status == 'working':
        lines.append('[!] No session on a working day.')

    return '\n'.join(lines)


def _chunk_message(text, limit=TELEGRAM_MESSAGE_LIMIT):
    """Split on message boundaries (blank lines) first, falling back to a
    hard cut only if a single message somehow exceeds the limit alone."""
    chunks = []
    current = ''
    for block in text.split('\n\n'):
        candidate = f'{current}\n\n{block}' if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block[:limit]
        while len(current) > limit:
            chunks.append(current[:limit])
            current = current[limit:]
    if current:
        chunks.append(current)
    return chunks or ['']


def _send_telegram_message(text):
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_GROUP_CHAT_ID
    if not token or not chat_id:
        log.error('TELEGRAM_BOT_TOKEN or TELEGRAM_GROUP_CHAT_ID is not set — skipping daily report send.')
        return False
    try:
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/sendMessage',
            json={'chat_id': chat_id, 'text': text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException:
        log.warning('Daily report Telegram send failed', exc_info=True)
        return False


def build_combined_report_text(date):
    employees = sorted(
        (u for u in AdminUser.objects.filter(is_active=True) if is_field_employee(u)),
        key=lambda u: (u.name or u.username),
    )
    reports = [format_employee_report(u, build_daily_report(u, date)) for u in employees]
    return '\n\n'.join(reports) if reports else 'No employees configured.'


def _reports_enabled_in_settings():
    """Settings > Business > Employee Reports toggle. Enabled by default if
    the setting row has never been saved (so existing installs don't go
    silent just because no one has touched this checkbox yet)."""
    from core.models import Setting

    value = Setting.objects.filter(
        setting_key='employee_telegram_report_enabled',
    ).values_list('setting_value', flat=True).first()
    return value is None or value.lower() == 'true'


def send_daily_report(date, dry_run=False, force=False):
    """
    Build + send the combined daily report for `date`. Idempotent per date:
    if a SENT log already exists and force is not set, this is a no-op.
    The Settings > Employee Reports toggle only gates the automatic send —
    `force=True` (an explicit admin resend) always goes through regardless.
    Returns (status, text) — status is one of TelegramReportLog.Status.
    """
    already_sent = TelegramReportLog.objects.filter(
        report_date=date, status=TelegramReportLog.Status.SENT,
    ).exists()
    if already_sent and not force:
        return TelegramReportLog.Status.SKIPPED, 'Already sent for this date.'

    text = build_combined_report_text(date)
    if dry_run:
        return TelegramReportLog.Status.SKIPPED, text

    if not force and not _reports_enabled_in_settings():
        return TelegramReportLog.Status.SKIPPED, 'Daily report sending is disabled in Settings > Employee Reports.'

    all_ok = bool(text)
    for chunk in _chunk_message(text):
        all_ok = _send_telegram_message(chunk) and all_ok

    status = TelegramReportLog.Status.SENT if all_ok else TelegramReportLog.Status.FAILED
    TelegramReportLog.objects.update_or_create(
        report_date=date,
        defaults={'status': status, 'detail': '' if all_ok else 'One or more message chunks failed to send.'},
    )
    return status, text
