"""
One-off backfill: generate `batch_number` for existing `Stock` rows that were
created before batch-number generation was wired into StocksAPI._restock.

Reproduces the same format as StocksAPI._build_batch_code
({VENDOR_CODE}{YY}{MM}{seq}), where `seq` is the row's rank (1, 2, 3, ...)
among all Stock rows for that flavor created in that calendar month, ordered
by created_at. Ranking against *all* rows in the group (not just the blank
ones) exactly reproduces what the live "count existing rows, +1" logic would
have produced had each row generated its code at creation time.

Batches with no vendor (or a vendor with no code) are skipped and reported,
since the format requires a vendor code.

Usage:
    python manage.py backfill_batch_numbers --dry-run
    python manage.py backfill_batch_numbers
"""
from collections import defaultdict

from django.core.management.base import BaseCommand

from products.models import Stock


class Command(BaseCommand):
    help = 'Backfill batch_number on existing Stock rows that have none.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without saving.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        all_rows = (
            Stock.objects.select_related('flavor', 'vendor')
            .order_by('created_at', 'id')
        )

        counters = defaultdict(int)
        updated, skipped, already_set = 0, 0, 0

        for stock in all_rows:
            on_date = stock.created_at.date()
            key = (stock.flavor_id, on_date.year, on_date.month)
            counters[key] += 1

            if stock.batch_number:
                already_set += 1
                continue

            vendor = stock.vendor
            if not vendor or not vendor.code:
                self.stdout.write(self.style.WARNING(
                    f'Skipping stock #{stock.id} ({stock.flavor.name}) — no vendor code.'
                ))
                skipped += 1
                continue

            seq = counters[key]
            code = f'{vendor.code.upper()}{on_date.strftime("%y")}{on_date.strftime("%m")}{seq}'

            self.stdout.write(f'stock #{stock.id} ({stock.flavor.name}, {on_date}) -> {code}')
            if not dry_run:
                stock.batch_number = code
                stock.save(update_fields=['batch_number'])
            updated += 1

        verb = 'Would update' if dry_run else 'Updated'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {updated} batch(es), skipped {skipped} (no vendor code), '
            f'{already_set} already had a batch number.'
        ))
