"""
Preview, send, or resend the daily employee work-session report that
normally fires automatically at 19:30 IST (sessions_tracking.tasks.run_daily_job).

Usage:
    python manage.py send_daily_report                      # today, send if not already sent
    python manage.py send_daily_report --date 2026-10-05
    python manage.py send_daily_report --dry-run             # print, never send/record
    python manage.py send_daily_report --resend              # force re-send + overwrite the log
    python manage.py send_daily_report --skip-autoclose       # report only, don't auto-close first
"""
from datetime import date as date_cls

from django.core.management.base import BaseCommand, CommandError

from sessions_tracking.services.autoclose import autoclose_open_sessions
from sessions_tracking.services.telegram import send_daily_report
from sessions_tracking.timeutil import ist_today


class Command(BaseCommand):
    help = 'Preview, send, or resend the daily employee work-session Telegram report.'

    def add_arguments(self, parser):
        parser.add_argument('--date', help='IST date, YYYY-MM-DD (default: today).')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Print the report text; never sends to Telegram or records a log entry.',
        )
        parser.add_argument(
            '--resend', action='store_true',
            help='Send even if already sent for this date, overwriting the log entry.',
        )
        parser.add_argument(
            '--skip-autoclose', action='store_true',
            help='Skip the auto-close step (useful when previewing a day already closed).',
        )

    def handle(self, *args, **options):
        if options['date']:
            try:
                day = date_cls.fromisoformat(options['date'])
            except ValueError:
                raise CommandError('--date must be YYYY-MM-DD.')
        else:
            day = ist_today()

        if not options['skip_autoclose']:
            closed = autoclose_open_sessions(day)
            self.stdout.write(f'Auto-closed {len(closed)} session(s) for {day}.')

        status, text = send_daily_report(day, dry_run=options['dry_run'], force=options['resend'])
        self.stdout.write(self.style.SUCCESS(f'Status: {status}'))
        self.stdout.write(text)
