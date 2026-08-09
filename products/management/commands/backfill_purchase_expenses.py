"""
One-off backfill: create `Expense` entries for existing `Stock` batches that
were restocked before StocksAPI._restock started auto-recording a linked
"Raw Materials" expense on every purchase.

The batch's *current* `quantity_grams` isn't reliable for this — it's been
decremented by any sales/movements since the batch was created. The original
purchased quantity is only preserved on the "in / purchase" StockMovement
recorded alongside the batch at restock time, so that's what this command
walks, mirroring `StocksAPI._record_purchase_expense`.

Skips:
  - movements whose stock already has a linked Expense (safe to re-run)
  - batches with no cost price recorded (no cost basis)

Usage:
    python manage.py backfill_purchase_expenses --dry-run
    python manage.py backfill_purchase_expenses
"""
from django.core.management.base import BaseCommand

from finance.models import Expense, ExpenseCategory
from products.models import StockMovement


class Command(BaseCommand):
    help = 'Backfill Expense entries for existing Stock purchases that have none.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be created without saving.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        category = None

        movements = (
            StockMovement.objects.filter(
                movement_type=StockMovement.MovementType.IN,
                reference_type=StockMovement.ReferenceType.PURCHASE,
                stock__isnull=False,
            )
            .select_related('stock', 'stock__flavor', 'stock__vendor', 'created_by')
            .order_by('created_at', 'id')
        )

        created, skipped_existing, skipped_no_cost = 0, 0, 0

        for m in movements:
            stock = m.stock
            if Expense.objects.filter(stock=stock).exists():
                skipped_existing += 1
                continue

            cost_price = m.cost_price_per_kg or stock.cost_price_per_kg
            if not cost_price or cost_price <= 0:
                self.stdout.write(self.style.WARNING(
                    f'Skipping stock #{stock.id} ({stock.flavor.name}) — no cost price.'
                ))
                skipped_no_cost += 1
                continue

            if category is None:
                category = ExpenseCategory.objects.filter(name__iexact='Raw Materials').order_by('id').first()
                if not category:
                    category, _ = ExpenseCategory.objects.get_or_create(
                        name='Raw Materials', defaults={'color': '#85AA4E'},
                    )

            qty_kg = round(m.quantity_grams / 1000, 3)
            amount = round(float(cost_price) * qty_kg, 2)
            expense_date = stock.last_restocked_date or m.created_at.date()
            vendor = stock.vendor

            self.stdout.write(
                f'stock #{stock.id} ({stock.flavor.name}, {qty_kg} kg @ ₹{cost_price}/kg, '
                f'{expense_date}) -> ₹{amount}'
            )
            if not dry_run:
                Expense.objects.create(
                    category=category,
                    stock=stock,
                    title=f'Stock purchase — {stock.flavor.name} ({qty_kg} kg)',
                    amount=amount,
                    expense_date=expense_date,
                    vendor_name=vendor.name if vendor else '',
                    vendor_contact=(vendor.phone or '') if vendor else '',
                    payment_method='cash',
                    payment_status='pending',
                    created_by=m.created_by,
                )
            created += 1

        verb = 'Would create' if dry_run else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {created} expense(s), skipped {skipped_existing} (already linked), '
            f'{skipped_no_cost} (no cost price).'
        ))
