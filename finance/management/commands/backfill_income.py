"""
One-off backfill: create `Income` ledger rows for existing B2C/B2B orders
that are already fully paid, ahead of `sync_b2c_order_income` /
`sync_b2b_order_income` keeping the ledger current going forward.

Only orders currently `payment_status='paid'` (and not cancelled/deleted)
are booked — unpaid, refunded, or cancelled orders are skipped, matching
the live sync's gate. Safe to re-run: `sync_*_order_income` is
`update_or_create`-keyed on the order, so already-backfilled orders are
just refreshed in place.

Usage:
    python manage.py backfill_income --dry-run
    python manage.py backfill_income
"""
from django.core.management.base import BaseCommand

from finance.models import Income
from finance.services import (
    B2B_INACTIVE_STATUSES,
    B2C_INACTIVE_STATUSES,
    sync_b2b_order_income,
    sync_b2c_order_income,
)
from orders.models import B2BOrder, Order


class Command(BaseCommand):
    help = 'Backfill Income ledger rows for existing paid B2C and B2B orders.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be created/updated without saving.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        b2c_orders = (
            Order.objects
            .filter(payment_status='paid')
            .exclude(status__in=B2C_INACTIVE_STATUSES)
            .order_by('order_date', 'id')
        )
        b2b_orders = (
            B2BOrder.objects
            .filter(payment_status='paid')
            .exclude(status__in=B2B_INACTIVE_STATUSES)
            .order_by('order_date', 'id')
        )

        before = Income.objects.count()
        b2c_count = b2c_orders.count()
        b2b_count = b2b_orders.count()

        if dry_run:
            self.stdout.write(
                f'Would sync {b2c_count} paid B2C order(s) and {b2b_count} paid B2B order(s).'
            )
            return

        for order in b2c_orders:
            sync_b2c_order_income(order)
        for order in b2b_orders:
            sync_b2b_order_income(order)

        after = Income.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'Synced {b2c_count} paid B2C order(s) and {b2b_count} paid B2B order(s). '
            f'Income rows: {before} -> {after}.'
        ))
