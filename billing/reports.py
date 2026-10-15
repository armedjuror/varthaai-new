"""
CA reports (Phase 2B): GSTR-1-shaped CSVs, sales / credit-note registers,
HSN summary, month close, and the e-invoicing turnover watch.

Section layout follows the GST offline tool's CSV templates (B2B, B2CL,
B2CS, CDNR, CDNUR, HSN split into B2B / B2C, DOCS). Values come only from
issued invoices and credit notes, never from orders.
"""
import csv
import io
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from billing import services as billing
from billing.gst_states import state_name
from billing.models import CreditNote, GSTPeriodClose, Invoice, InvoiceSeries
from billing.tax import ZERO

B2CL_THRESHOLD = Decimal('100000')      # unregistered inter-state invoices above this go to B2CL
EINVOICE_THRESHOLD = Decimal('50000000')  # ₹5 crore aggregate turnover


def gst_date(d):
    return d.strftime('%d-%b-%Y')


def pos_text(code):
    return f'{code}-{state_name(code)}' if code else ''


def month_bounds(period):
    year, month = (int(x) for x in period.split('-'))
    start = date(year, month, 1)
    end = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return start, end


def _invoices(start, end):
    return (Invoice.objects.filter(invoice_date__gte=start, invoice_date__lte=end)
            .prefetch_related('lines').order_by('invoice_date', 'id'))


def _credit_notes(start, end):
    return (CreditNote.objects.filter(credit_note_date__gte=start, credit_note_date__lte=end)
            .select_related('invoice').prefetch_related('lines__invoice_line').order_by('credit_note_date', 'id'))


def _is_b2cl(inv):
    return not inv.bill_to_gstin and inv.is_interstate and inv.grand_total > B2CL_THRESHOLD


def _by_rate(lines, line_of=lambda ln: ln):
    """{rate: {'taxable', 'cess'}} over invoice or credit-note lines."""
    out = defaultdict(lambda: {'taxable': ZERO, 'cess': ZERO, 'igst': ZERO, 'cgst': ZERO, 'sgst': ZERO})
    for ln in lines:
        rate = line_of(ln).gst_rate
        out[rate]['taxable'] += ln.taxable_value
        out[rate]['cess'] += ln.cess_amount
        out[rate]['igst'] += ln.igst_amount
        out[rate]['cgst'] += ln.cgst_amount
        out[rate]['sgst'] += ln.sgst_amount
    return out


def gstr1_sections(start, end):
    invoices = list(_invoices(start, end))
    cns = list(_credit_notes(start, end))
    b2b, b2cl, cdnr, cdnur = [], [], [], []
    b2cs = defaultdict(lambda: {'taxable': ZERO, 'cess': ZERO})
    for inv in invoices:
        rates = _by_rate(inv.lines.all())
        for rate, t in sorted(rates.items()):
            if inv.bill_to_gstin:
                b2b.append([inv.bill_to_gstin, inv.bill_to_name, inv.number, gst_date(inv.invoice_date),
                            inv.grand_total, pos_text(inv.place_of_supply_state), 'N', '', 'Regular B2B', '',
                            rate, t['taxable'], t['cess']])
            elif _is_b2cl(inv):
                b2cl.append([inv.number, gst_date(inv.invoice_date), inv.grand_total,
                             pos_text(inv.place_of_supply_state), '', rate, t['taxable'], t['cess'], ''])
            else:
                key = (inv.place_of_supply_state, rate)
                b2cs[key]['taxable'] += t['taxable']
                b2cs[key]['cess'] += t['cess']
    for cn in cns:
        inv = cn.invoice
        rates = _by_rate(cn.lines.all(), lambda cl: cl.invoice_line)
        for rate, t in sorted(rates.items()):
            if inv.bill_to_gstin:
                cdnr.append([inv.bill_to_gstin, inv.bill_to_name, cn.number, gst_date(cn.credit_note_date), 'C',
                             pos_text(inv.place_of_supply_state), 'N', 'Regular B2B', cn.grand_total, '',
                             rate, t['taxable'], t['cess']])
            elif _is_b2cl(inv):
                cdnur.append(['B2CL', cn.number, gst_date(cn.credit_note_date), 'C',
                              pos_text(inv.place_of_supply_state), cn.grand_total, '', rate, t['taxable'], t['cess']])
            else:
                # Credit notes on small B2C invoices reduce B2CS for the period.
                key = (inv.place_of_supply_state, rate)
                b2cs[key]['taxable'] -= t['taxable']
                b2cs[key]['cess'] -= t['cess']
    b2cs_rows = [['OE', pos_text(pos), '', rate, v['taxable'], v['cess'], '']
                 for (pos, rate), v in sorted(b2cs.items()) if v['taxable'] or v['cess']]
    return {
        'b2b': (['GSTIN/UIN of Recipient', 'Receiver Name', 'Invoice Number', 'Invoice date', 'Invoice Value',
                 'Place Of Supply', 'Reverse Charge', 'Applicable % of Tax Rate', 'Invoice Type',
                 'E-Commerce GSTIN', 'Rate', 'Taxable Value', 'Cess Amount'], b2b),
        'b2cl': (['Invoice Number', 'Invoice date', 'Invoice Value', 'Place Of Supply', 'Applicable % of Tax Rate',
                  'Rate', 'Taxable Value', 'Cess Amount', 'E-Commerce GSTIN'], b2cl),
        'b2cs': (['Type', 'Place Of Supply', 'Applicable % of Tax Rate', 'Rate', 'Taxable Value', 'Cess Amount',
                  'E-Commerce GSTIN'], b2cs_rows),
        'cdnr': (['GSTIN/UIN of Recipient', 'Receiver Name', 'Note Number', 'Note Date', 'Note Type',
                  'Place Of Supply', 'Reverse Charge', 'Note Supply Type', 'Note Value',
                  'Applicable % of Tax Rate', 'Rate', 'Taxable Value', 'Cess Amount'], cdnr),
        'cdnur': (['UR Type', 'Note Number', 'Note Date', 'Note Type', 'Place Of Supply', 'Note Value',
                   'Applicable % of Tax Rate', 'Rate', 'Taxable Value', 'Cess Amount'], cdnur),
        'hsn_b2b': hsn_rows(invoices, cns, registered=True),
        'hsn_b2c': hsn_rows(invoices, cns, registered=False),
        'docs': docs_rows(invoices, cns),
    }


def hsn_rows(invoices, cns, registered):
    from billing.models import HSNCode

    descriptions = dict(HSNCode.objects.values_list('code', 'description'))
    groups = defaultdict(lambda: {'uqc': '', 'qty': Decimal('0'), 'value': ZERO, 'taxable': ZERO,
                                  'igst': ZERO, 'cgst': ZERO, 'sgst': ZERO, 'cess': ZERO})

    def add(line, src, sign):
        g = groups[(src.hsn_code, src.gst_rate)]
        g['uqc'] = g['uqc'] or src.uqc
        if src.kind == 'GOODS':
            g['qty'] += sign * Decimal(line.quantity)
        g['value'] += sign * line.total_amount
        g['taxable'] += sign * line.taxable_value
        for k, attr in (('igst', 'igst_amount'), ('cgst', 'cgst_amount'), ('sgst', 'sgst_amount'),
                        ('cess', 'cess_amount')):
            g[k] += sign * getattr(line, attr)

    for inv in invoices:
        if bool(inv.bill_to_gstin) == registered:
            for ln in inv.lines.all():
                add(ln, ln, 1)
    for cn in cns:
        if bool(cn.invoice.bill_to_gstin) == registered:
            for cl in cn.lines.all():
                add(cl, cl.invoice_line, -1)
    rows = [[hsn, descriptions.get(hsn, ''), (g['uqc'] or 'OTH') + '-' + {
        'KGS': 'KILOGRAMS', 'PCS': 'PIECES', 'NOS': 'NUMBERS', 'PAC': 'PACKS'}.get(g['uqc'], 'OTHERS'),
        g['qty'], g['value'], rate, g['taxable'], g['igst'], g['cgst'], g['sgst'], g['cess']]
        for (hsn, rate), g in sorted(groups.items())]
    return (['HSN', 'Description', 'UQC', 'Total Quantity', 'Total Value', 'Rate', 'Taxable Value',
             'Integrated Tax Amount', 'Central Tax Amount', 'State/UT Tax Amount', 'Cess Amount'], rows)


def docs_rows(invoices, cns):
    rows = []
    for label, docs in (('Invoices for outward supply', invoices), ('Credit Note', cns)):
        by_series = defaultdict(list)
        for d in docs:
            by_series[d.series_id].append(d.number)
        for numbers in by_series.values():
            numbers.sort()
            rows.append([label, numbers[0], numbers[-1], len(numbers), 0])
    return (['Nature of Document', 'Sr. No. From', 'Sr. No. To', 'Total Number', 'Cancelled'], rows)


def csv_bytes(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


def gstr1_zip(start, end):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, (header, rows) in gstr1_sections(start, end).items():
            zf.writestr(f'{name}.csv', csv_bytes(header, rows))
        zf.writestr('README.txt', (
            f'GSTR-1 data for {start:%d-%m-%Y} to {end:%d-%m-%Y}, from issued tax invoices and credit notes.\n'
            'Columns follow the GST offline tool CSV templates. Check with your CA before uploading.\n'
            'B2CL = unregistered inter-state invoices above Rs 1,00,000; credit notes on small B2C invoices '
            'are netted into B2CS. Invoices cancelled and reissued are reported with their credit notes.\n'
        ))
    return buf.getvalue()


def sales_register(start, end):
    rows = []
    for inv in _invoices(start, end):
        for rate, t in sorted(_by_rate(inv.lines.all()).items()):
            rows.append([inv.number, inv.invoice_date.isoformat(), inv.supply_type, inv.bill_to_name,
                         inv.bill_to_gstin, pos_text(inv.place_of_supply_state), inv.order_id, rate, t['taxable'],
                         t['cgst'], t['sgst'], t['igst'], t['cess'], inv.grand_total, inv.status])
    return (['Invoice No', 'Date', 'Type', 'Customer', 'GSTIN', 'Place of Supply', 'Order', 'Rate', 'Taxable',
             'CGST', 'SGST', 'IGST', 'Cess', 'Invoice Total', 'Status'], rows)


def credit_note_register(start, end):
    rows = []
    for cn in _credit_notes(start, end):
        inv = cn.invoice
        for rate, t in sorted(_by_rate(cn.lines.all(), lambda cl: cl.invoice_line).items()):
            rows.append([cn.number, cn.credit_note_date.isoformat(), inv.number, inv.invoice_date.isoformat(),
                         inv.bill_to_name, inv.bill_to_gstin, pos_text(inv.place_of_supply_state),
                         cn.get_reason_display(), rate, t['taxable'], t['cgst'], t['sgst'], t['igst'], t['cess'],
                         cn.grand_total])
    return (['Credit Note No', 'Date', 'Invoice No', 'Invoice Date', 'Customer', 'GSTIN', 'Place of Supply',
             'Reason', 'Rate', 'Taxable', 'CGST', 'SGST', 'IGST', 'Cess', 'Credit Note Total'], rows)


def hsn_summary_csv(start, end):
    invoices = list(_invoices(start, end))
    cns = list(_credit_notes(start, end))
    header, b2b = hsn_rows(invoices, cns, True)
    _, b2c = hsn_rows(invoices, cns, False)
    return (['Section'] + header, [['B2B'] + r for r in b2b] + [['B2C'] + r for r in b2c])


REPORTS = {
    'sales-register': sales_register,
    'credit-note-register': credit_note_register,
    'hsn-summary': hsn_summary_csv,
}


# ── period totals / month close ──────────────────────────────────────────

def period_totals(start, end):
    inv = Invoice.objects.filter(invoice_date__gte=start, invoice_date__lte=end)
    cn = CreditNote.objects.filter(credit_note_date__gte=start, credit_note_date__lte=end)
    fields = {'taxable': Sum('taxable_total'), 'cgst': Sum('cgst_total'), 'sgst': Sum('sgst_total'),
              'igst': Sum('igst_total'), 'cess': Sum('cess_total'), 'total': Sum('grand_total')}
    i, c = inv.aggregate(**fields), cn.aggregate(**fields)
    out = {'invoices': inv.count(), 'credit_notes': cn.count()}
    for key in fields:
        out[f'invoice_{key}'] = str(i[key] or ZERO)
        out[f'credit_note_{key}'] = str(c[key] or ZERO)
        out[f'net_{key}'] = str((i[key] or ZERO) - (c[key] or ZERO))
    return out


def is_closed(entity, d):
    return GSTPeriodClose.objects.filter(entity=entity, period=d.strftime('%Y-%m')).exists()


def close_period(entity, period, user):
    start, end = month_bounds(period)
    if end >= billing.ist_today():
        raise billing.BillingError('A month can only be closed after it has ended.')
    with transaction.atomic():
        row, created = GSTPeriodClose.objects.get_or_create(
            entity=entity, period=period,
            defaults={'totals': period_totals(start, end), 'closed_by': user if getattr(user, 'pk', None) else None},
        )
    if not created:
        raise billing.BillingError(f'{period} is already closed.')
    billing._activity(user, f'GST period {period} closed', str(row.totals))
    return row


def reopen_period(entity, period, user):
    deleted, _ = GSTPeriodClose.objects.filter(entity=entity, period=period).delete()
    if not deleted:
        raise billing.BillingError(f'{period} is not closed.')
    billing._activity(user, f'GST period {period} reopened', '')


def closed_periods(entity):
    return [{
        'period': p.period, 'closed_at': p.closed_at.isoformat(),
        'closed_by': p.closed_by.name if p.closed_by else '', 'totals': p.totals,
    } for p in GSTPeriodClose.objects.filter(entity=entity).select_related('closed_by').order_by('-period')]


# ── turnover watch ───────────────────────────────────────────────────────

def turnover(entity, on_date=None):
    """Aggregate turnover for the financial year so far (taxable value of
    invoices less credit notes) against the ₹5 crore e-invoicing limit."""
    on_date = on_date or billing.ist_today()
    fy = billing.fiscal_year(on_date)
    start = date(int(fy[:4]), 4, 1)
    inv = Invoice.objects.filter(entity=entity, invoice_date__gte=start, invoice_date__lte=on_date)
    cn = CreditNote.objects.filter(entity=entity, credit_note_date__gte=start, credit_note_date__lte=on_date)
    total = ((inv.aggregate(t=Sum('taxable_total'))['t'] or ZERO)
             - (cn.aggregate(t=Sum('taxable_total'))['t'] or ZERO))
    pct = float(total / EINVOICE_THRESHOLD * 100) if total > 0 else 0.0
    return {
        'fiscal_year': fy, 'turnover': float(total), 'threshold': float(EINVOICE_THRESHOLD),
        'percent': round(pct, 2), 'warning': pct >= 80,
        'note': 'Counts GST invoices in this app only. Add other supplies under the same PAN (if any) yourself.',
    }


def series_summary():
    return [{'series': f'{s.get_doc_type_display()} {s.fiscal_year}', 'last': s.last_number}
            for s in InvoiceSeries.objects.order_by('-fiscal_year', 'doc_type')]
