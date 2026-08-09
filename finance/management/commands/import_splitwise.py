"""
One-off import of the cleaned Splitwise export (see lab/splitwise/)
into Expense and Investment. Idempotent — safe to re-run: rows already
present (matched on title/partner + date + amount) are skipped rather than
duplicated.

  python manage.py import_splitwise --dry-run
  python manage.py import_splitwise
"""
import csv
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from accounts.models import AdminUser
from finance.models import Expense, ExpenseCategory, Investment

IMPORT_DIR = os.path.join(settings.BASE_DIR, 'lab', 'splitwise')


class Command(BaseCommand):
    help = 'Import lab/splitwise/expenses_regular.csv and investments.csv.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show what would be created without saving.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created_by = AdminUser.objects.filter(username='ajwad').first()

        exp_created, exp_skipped = self._import_expenses(dry_run, created_by)
        inv_created, inv_skipped = self._import_investments(dry_run, created_by)

        verb = 'Would create' if dry_run else 'Created'
        self.stdout.write(self.style.SUCCESS(
            f'\nExpenses: {verb} {exp_created}, skipped {exp_skipped} (already imported).\n'
            f'Investments: {verb} {inv_created}, skipped {inv_skipped} (already imported).'
        ))

    def _import_expenses(self, dry_run, created_by):
        path = os.path.join(IMPORT_DIR, 'expenses_regular.csv')
        if not os.path.exists(path):
            raise CommandError(f'Not found: {path}')

        created, skipped = 0, 0
        with open(path, newline='') as f:
            rows = list(csv.DictReader(f))

        for r in rows:
            title = r['title'].strip()
            date = r['date'].strip()
            amount = float(r['amount'])

            # Match only against rows this command itself created — matching on
            # title/date/amount alone can false-positive against unrelated
            # pre-existing data that happens to share those fields (e.g. a
            # manually entered "Domain" expense in a different category).
            if Expense.objects.filter(
                title=title, expense_date=date, amount=amount,
                notes__icontains='Imported from Splitwise',
            ).exists():
                skipped += 1
                continue

            category = (
                ExpenseCategory.objects.filter(name__iexact=r['category']).order_by('id').first()
            )
            self.stdout.write(f'  expense: {date}  {title}  ({r["category"]})  ₹{amount}')
            if not dry_run:
                if category is None:
                    category, _ = ExpenseCategory.objects.get_or_create(name=r['category'])
                Expense.objects.create(
                    category=category,
                    title=title,
                    amount=amount,
                    expense_date=date,
                    payment_method=r['payment_method'],
                    payment_status=r['payment_status'],
                    notes='Imported from Splitwise export (2024-11 to 2026-01).',
                    created_by=created_by,
                )
            created += 1
        return created, skipped

    def _import_investments(self, dry_run, created_by):
        path = os.path.join(IMPORT_DIR, 'investments.csv')
        if not os.path.exists(path):
            raise CommandError(f'Not found: {path}')

        created, skipped = 0, 0
        with open(path, newline='') as f:
            rows = list(csv.DictReader(f))

        for r in rows:
            partner = r['partner_name'].strip()
            date = r['investment_date'].strip()
            amount = float(r['amount'])

            if Investment.objects.filter(partner_name=partner, investment_date=date, amount=amount).exists():
                skipped += 1
                continue

            self.stdout.write(f'  investment: {date}  {partner}  ₹{amount}')
            if not dry_run:
                Investment.objects.create(
                    partner_name=partner,
                    amount=amount,
                    investment_date=date,
                    description=r['description'],
                    payment_method=r['payment_method'],
                    reference_number=r['reference_number'],
                    notes=r['notes'],
                    created_by=created_by,
                )
            created += 1
        return created, skipped
