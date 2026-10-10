"""
Issue GST invoices for orders dispatched since GST billing started but before
invoicing went live (the October 2026 backfill).

    python manage.py backfill_invoices --from 2026-10-01 --dry-run
    python manage.py backfill_invoices --from 2026-10-01
    python manage.py backfill_invoices --from 2026-10-01 --date VOB_abc=2026-10-04 --exclusive VOB_abc

Orders get invoices in dispatch-date order, each dated on its dispatch date,
in INCLUSIVE price mode (the customer already paid / agreed the old price).
`--exclusive ORDER_ID` switches an unpaid B2B order to GST-on-top; its total
then grows by the GST (only do this when the buyer has agreed).

The app does not record a dispatch timestamp, so the date is estimated:
  B2C: when its stock was deducted (that happens on ship / deliver),
       else the order's last update.
  B2B: delivered_at for delivered orders, else the order's last update.
Check the dry run and correct any date with --date ORDER_ID=YYYY-MM-DD.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from billing import services as billing
from billing.gst_states import state_from_pincode, state_label
from billing.models import Invoice


class Command(BaseCommand):
    help = 'Issue GST invoices for dispatched orders that do not have one (dispatch-date order).'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='date_from', required=True, help='First dispatch date (YYYY-MM-DD).')
        parser.add_argument('--to', dest='date_to', help='Last dispatch date (default: today, IST).')
        parser.add_argument('--dry-run', action='store_true', help='Show what would be issued; change nothing.')
        parser.add_argument('--date', action='append', default=[], metavar='ORDER_ID=YYYY-MM-DD',
                            help='Override the estimated dispatch date of an order (repeatable).')
        parser.add_argument('--exclusive', action='append', default=[], metavar='ORDER_ID',
                            help='Invoice this unpaid B2B order with GST on top (repeatable).')
        parser.add_argument('--allow-out-of-order', action='store_true',
                            help='Issue even if later-dated invoices already exist (numbers will not follow dates).')

    def handle(self, *args, **opts):
        start = billing.parse_iso_date(opts['date_from'])
        end = billing.parse_iso_date(opts['date_to']) if opts['date_to'] else billing.ist_today()
        if not start or not end:
            raise CommandError('Dates must be YYYY-MM-DD.')
        overrides = {}
        for item in opts['date']:
            order_id, _, raw = item.partition('=')
            parsed = billing.parse_iso_date(raw)
            if not parsed:
                raise CommandError(f'Bad --date {item!r}; use ORDER_ID=YYYY-MM-DD.')
            overrides[order_id.strip()] = parsed
        exclusive = {x.strip() for x in opts['exclusive']}

        candidates = sorted(self._candidates(start, end, overrides), key=lambda c: (c['date'], c['when'], c['id']))
        if not candidates:
            self.stdout.write('Nothing to backfill.')
            return

        later = Invoice.objects.filter(invoice_date__gt=candidates[0]['date']).order_by('invoice_date').first()
        if later and not opts['allow_out_of_order']:
            raise CommandError(
                f'Invoice {later.number} dated {later.invoice_date} already exists; backfilling earlier dates now '
                'would put numbers out of date order. Re-run with --allow-out-of-order to accept that.'
            )

        self.stdout.write(f'{"Order":<22} {"Type":<4} {"Dispatch":<11} {"Date from":<16} {"State":<24} {"Mode":<9} Result')
        issued = failed = 0
        for c in candidates:
            order = c['order']
            mode = 'exclusive' if c['id'] in exclusive else 'inclusive'
            note = ''
            if c['kind'] == 'B2C' and not order.shipping_state_code:
                derived = state_from_pincode(order.pincode)
                note = f'state from pincode {order.pincode}' if derived else 'NO STATE — set it on the order'
                state = derived
            else:
                state = order.shipping_state_code if c['kind'] == 'B2C' else billing.b2b_place_of_supply(order)
            if mode == 'exclusive' and (c['kind'] != 'B2B' or order.paid_amount > 0):
                result = 'SKIP: --exclusive is only for unpaid B2B orders'
                failed += 1
            elif opts['dry_run']:
                result = self._dry_run(order, c, state, mode) + (f' ({note})' if note else '')
            else:
                result = self._issue(order, c, state, mode)
                if result.startswith('ERROR'):
                    failed += 1
                else:
                    issued += 1
            self.stdout.write(f'{c["id"]:<22} {c["kind"]:<4} {c["date"]!s:<11} {c["source"]:<16} '
                              f'{state_label(state) or "?":<24} {mode:<9} {result}')
        if opts['dry_run']:
            self.stdout.write(self.style.WARNING(f'Dry run: {len(candidates)} order(s) would be invoiced. Nothing changed.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Issued {issued} invoice(s); {failed} failed or skipped.'))

    # ── selection ────────────────────────────────────────────────────────
    def _candidates(self, start, end, overrides):
        from orders.models import B2BOrder, Order
        from products.models import StockMovement

        for o in Order.objects.filter(status__in=billing.B2C_INVOICE_STATUSES, invoices__isnull=True):
            moved = (StockMovement.objects.filter(reference_id=o.id, movement_type='out')
                     .order_by('created_at').values_list('created_at', flat=True).first())
            when, source = (moved, 'stock deducted') if moved else (o.updated_at, 'last update')
            yield from self._keep(o, 'B2C', when, source, start, end, overrides)
        for o in (B2BOrder.objects.filter(status__in=billing.B2B_INVOICE_STATUSES, invoices__isnull=True)
                  .select_related('company')):
            when, source = (o.delivered_at, 'delivered_at') if o.delivered_at else (o.updated_at, 'last update')
            yield from self._keep(o, 'B2B', when, source, start, end, overrides)

    def _keep(self, order, kind, when, source, start, end, overrides):
        d = billing.ist_date(when)
        if order.pk in overrides:
            d, source = overrides[order.pk], 'override'
        if d and start <= d <= end and billing.gst_active(order.brand_id, d):
            yield {'order': order, 'id': order.pk, 'kind': kind, 'date': d, 'when': when, 'source': source}

    # ── issue ────────────────────────────────────────────────────────────
    def _prepare(self, order, kind, state, mode):
        if kind == 'B2C':
            if not order.shipping_state_code and state:
                order.shipping_state_code = state
                order.save(update_fields=['shipping_state_code'])
            if not order.price_mode:
                order.price_mode = 'inclusive'
                order.save(update_fields=['price_mode'])
        elif order.price_mode != mode:
            order.price_mode = mode
            order.save(update_fields=['price_mode'])

    def _dry_run(self, order, c, state, mode):
        try:
            with transaction.atomic():
                self._prepare(order, c['kind'], state, mode)
                inv = billing.ensure_invoice(order, None, on_date=c['date'])
                text = f'would issue ₹{inv.grand_total} (GST ₹{inv.tax_total})'
                transaction.set_rollback(True)
            return text
        except billing.BillingError as exc:
            return f'ERROR: {exc}'

    def _issue(self, order, c, state, mode):
        try:
            with transaction.atomic():
                self._prepare(order, c['kind'], state, mode)
                inv = billing.ensure_invoice(order, None, on_date=c['date'])
            return f'{inv.number} ₹{inv.grand_total}'
        except billing.BillingError as exc:
            return f'ERROR: {exc}'

