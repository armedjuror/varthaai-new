"""
Remove GST test documents created while trying GST billing on the live site,
and give their numbers back so real numbering starts where it should.

    python manage.py gst_test_cleanup --order VOB_abc --order VO_xyz            # dry run (default)
    python manage.py gst_test_cleanup --order VOB_abc --order VO_xyz --yes      # do it
    python manage.py gst_test_cleanup --order VOB_abc --yes --delete-orders --restock

What it deletes for the named orders only: their tax invoices, credit notes
and lines, refunds records, share links, link payment attempts, WhatsApp
message logs and the stored PDFs. Then each touched series is reset to the
highest number still in use (0 when nothing real was issued), so the next
real invoice gets the next number with no gap.

Safety checks (it refuses and changes nothing when one fails):
  - every named order must exist;
  - in each series it touches, the test documents must be the LAST numbers
    issued — if any other order's invoice / credit note came after them,
    deleting would leave a gap, so it stops;
  - no touched document may be in a closed GST month.

--delete-orders also deletes the test orders themselves (and, for B2B, their
payments, returns and the order's activity entries); --restock first puts
back stock still deducted for them. Money taken with Razorpay is NOT
refunded by this command — refund it in the Razorpay dashboard.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from billing import services as billing
from billing.models import (
    CreditNote, CreditNoteLine, GSTPeriodClose, Invoice, InvoiceLine, InvoiceSeries, MessageLog,
    PaymentAttempt, Refund, ShareLink,
)

def _seq(number):
    return int(number.rsplit('/', 1)[-1])


class Command(BaseCommand):
    help = 'Delete GST test invoices/credit notes for named orders and reset numbering (dry run by default).'

    def add_arguments(self, parser):
        parser.add_argument('--order', action='append', default=[], required=True, metavar='ORDER_ID',
                            help='Test order id (B2C or B2B). Repeat for several.')
        parser.add_argument('--yes', action='store_true', help='Actually delete. Without it, only show the plan.')
        parser.add_argument('--delete-orders', action='store_true', help='Also delete the test orders themselves.')
        parser.add_argument('--restock', action='store_true',
                            help='With --delete-orders: put back stock still deducted for these orders.')

    def handle(self, *args, **opts):
        from orders.models import B2BOrder, Order

        ids = list(dict.fromkeys(o.strip() for o in opts['order'] if o.strip()))
        b2c = {o.pk: o for o in Order.objects.filter(pk__in=ids)}
        b2b = {o.pk: o for o in B2BOrder.objects.filter(pk__in=ids)}
        missing = [i for i in ids if i not in b2c and i not in b2b]
        if missing:
            raise CommandError(f'Order(s) not found: {", ".join(missing)}')

        invoices = list(Invoice.objects.filter(b2c_order_id__in=list(b2c)) | Invoice.objects.filter(
            b2b_order_id__in=list(b2b)))
        credit_notes = list(CreditNote.objects.filter(invoice__in=invoices))
        self._check_tail(invoices, credit_notes)
        self._check_closed(invoices, credit_notes)

        self.stdout.write('Test orders: ' + ', '.join(ids))
        for inv in sorted(invoices, key=lambda i: i.number):
            self.stdout.write(f'  invoice     {inv.number:<16} {inv.invoice_date}  ₹{inv.grand_total}  ({inv.order_id})')
        for cn in sorted(credit_notes, key=lambda c: c.number):
            self.stdout.write(f'  credit note {cn.number:<16} {cn.credit_note_date}  ₹{cn.grand_total}')
        resets = self._resets(invoices, credit_notes)
        for series, new_last in resets.items():
            nxt = billing.format_number(series, new_last + 1)
            self.stdout.write(f'  series {series.doc_type} {series.fiscal_year}: last {series.last_number} → '
                              f'{new_last} (next number {nxt})')
        if opts['delete_orders']:
            self.stdout.write(f'  orders to delete: {", ".join(ids)}' + (' (with restock)' if opts['restock'] else ''))
        if not opts['yes']:
            self.stdout.write(self.style.WARNING('Dry run — nothing changed. Re-run with --yes to delete.'))
            return

        with transaction.atomic():
            # Grams already put back by credit notes (read before they go).
            credited_back = defaultdict(int)
            for cl in CreditNoteLine.objects.filter(credit_note__in=credit_notes, restock=True,
                                                    invoice_line__b2c_item__isnull=False):
                credited_back[cl.invoice_line.b2c_item_id] += cl.source_quantity
            self._delete(invoices, credit_notes, list(b2c), list(b2b))
            for series, new_last in resets.items():
                InvoiceSeries.objects.filter(pk=series.pk).update(last_number=new_last)
            if opts['delete_orders']:
                self._delete_orders(list(b2c.values()), list(b2b.values()), opts['restock'], credited_back)
            else:
                from finance.services import sync_b2b_order_income, sync_b2c_order_income

                for o in Order.objects.filter(pk__in=list(b2c)):
                    sync_b2c_order_income(o)
                for o in B2BOrder.objects.filter(pk__in=list(b2b)):
                    sync_b2b_order_income(o)
            billing._activity(None, 'GST test data removed',
                              f'Orders {", ".join(ids)}; {len(invoices)} invoice(s), {len(credit_notes)} credit note(s)')
        for doc in invoices + credit_notes:
            if doc.pdf_file:
                try:
                    doc.pdf_file.storage.delete(doc.pdf_file.name)
                except OSError:
                    pass
        self.stdout.write(self.style.SUCCESS('Done.'))

    # ── checks ────────────────────────────────────────────────────────────
    def _check_tail(self, invoices, credit_notes):
        test_ids = {('inv', d.pk) for d in invoices} | {('cn', d.pk) for d in credit_notes}
        by_series = defaultdict(list)
        for d in invoices:
            by_series[d.series_id].append(('inv', d))
        for d in credit_notes:
            by_series[d.series_id].append(('cn', d))
        for series_id, docs in by_series.items():
            lowest = min(_seq(d.number) for _, d in docs)
            model = Invoice if docs[0][0] == 'inv' else CreditNote
            tag = docs[0][0]
            later = [d for d in model.objects.filter(series_id=series_id)
                     if _seq(d.number) >= lowest and (tag, d.pk) not in test_ids]
            if later:
                raise CommandError(
                    'Refusing: real document(s) were issued after the test ones in the same series: '
                    + ', '.join(f'{d.number} ({d.order_id})' for d in later)
                    + '. Deleting the test documents would leave a gap in the numbering. '
                    'Keep them and cancel with credit notes instead.'
                )

    def _check_closed(self, invoices, credit_notes):
        for d, when in [(i, i.invoice_date) for i in invoices] + [(c, c.credit_note_date) for c in credit_notes]:
            if GSTPeriodClose.objects.filter(entity_id=d.entity_id, period=when.strftime('%Y-%m')).exists():
                raise CommandError(f'{d.number} is in closed GST month {when:%Y-%m}; reopen it first.')

    def _resets(self, invoices, credit_notes):
        test_pks = {'INV': {d.pk for d in invoices}, 'CN': {d.pk for d in credit_notes}}
        out = {}
        for series in InvoiceSeries.objects.filter(pk__in={d.series_id for d in invoices + credit_notes}):
            model = Invoice if series.doc_type == 'INV' else CreditNote
            remaining = [_seq(n) for pk, n in model.objects.filter(series=series).values_list('pk', 'number')
                         if pk not in test_pks[series.doc_type]]
            out[series] = max(remaining, default=0)
        return out

    # ── deletion ──────────────────────────────────────────────────────────
    def _allow_cleanup(self, on):
        """Let the immutability trigger pass deletes inside THIS transaction
        only (SET LOCAL; switched off again right after the deletes)."""
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL varthaai.gst_cleanup = '{'on' if on else 'off'}'")

    def _delete(self, invoices, credit_notes, b2c_ids, b2b_ids):
        from orders.models import B2BOrder, B2BReturn

        self._allow_cleanup(True)
        Refund.objects.filter(order_id__in=b2c_ids).delete()
        B2BReturn.objects.filter(credit_note__in=credit_notes).update(credit_note=None, needs_credit_note=False)
        CreditNoteLine.objects.filter(credit_note__in=credit_notes).delete()
        CreditNote.objects.filter(pk__in=[c.pk for c in credit_notes]).delete()
        InvoiceLine.objects.filter(invoice__in=invoices).delete()
        # Newest first: a reissued invoice points at the one it replaced.
        for inv in sorted(invoices, key=lambda i: i.pk, reverse=True):
            Invoice.objects.filter(pk=inv.pk).delete()
        self._allow_cleanup(False)
        for model in (ShareLink, PaymentAttempt, MessageLog):
            model.objects.filter(b2c_order_id__in=b2c_ids).delete()
            model.objects.filter(b2b_order_id__in=b2b_ids).delete()
        B2BOrder.objects.filter(pk__in=b2b_ids).update(invoice_number='')

    def _delete_orders(self, b2c_orders, b2b_orders, restock, credited_back):
        from crm.models import B2BActivity
        from orders.models import B2BPayment
        from products.services import restock_batch, revert_stock

        for o in b2b_orders:
            if restock and o.stock_deducted:
                restocked = defaultdict(int)
                for r in o.returns.all():
                    for row in r.items:
                        if row.get('restock', True):
                            restocked[row.get('item_id')] += int(row.get('quantity') or 0)
                for it in o.items.select_related('flavor', 'stock'):
                    grams = (it.quantity - restocked.get(it.id, 0)) * it.weight_grams
                    if grams <= 0:
                        continue
                    kw = dict(reference_type='b2b_reversal', reference_id=o.pk,
                              notes=f'GST test order {o.pk} removed')
                    if it.stock_id and it.stock:
                        restock_batch(it.stock, grams, **kw)
                    else:
                        revert_stock(it.flavor, grams, **kw)
            B2BActivity.objects.filter(order=o).delete()
            B2BPayment.objects.filter(b2b_order=o).delete()
            o.returns.all().delete()
            o.items.all().delete()
            o.delete()
        for o in b2c_orders:
            if restock and o.stock_deducted:
                for it in o.items.select_related('flavor'):
                    grams = it.quantity - credited_back.get(it.id, 0)
                    if it.flavor and grams > 0:
                        revert_stock(it.flavor, grams, reference_type='sale', reference_id=o.pk,
                                     notes=f'GST test order {o.pk} removed')
            o.delete()
