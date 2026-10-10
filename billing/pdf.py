"""
Tax Invoice / Credit Note rendering. One A4 monochrome template serves the
browser print view and the stored PDF (WeasyPrint), always driven from the
stored document — never recomputed from the order.
"""
from decimal import Decimal

from django.conf import settings
from django.template.loader import render_to_string

from billing.gst_states import state_label, state_name
from billing.tax import hsn_summary


def amount_in_words(amount):
    """Indian-style rupees in words: 'Rupees One Lakh Twenty Thousand and
    Fifty Paise Only'."""
    from num2words import num2words

    amount = Decimal(amount).quantize(Decimal('0.01'))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = 'Rupees ' + num2words(rupees, lang='en_IN').replace(',', '').replace('-', ' ').title()
    if paise:
        words += ' and ' + num2words(paise, lang='en_IN').replace('-', ' ').title() + ' Paise'
    return words.replace(' And ', ' ') + ' Only'


def _copies(copies):
    from billing.services import COPY_LABELS

    copies = [c for c in (copies or ['original']) if c in COPY_LABELS] or ['original']
    return [COPY_LABELS[c] for c in copies]


def invoice_context(invoice, copies=None, for_pdf=False):
    lines = list(invoice.lines.all())
    return {
        'doc': invoice, 'invoice': invoice, 'lines': lines, 'is_credit_note': False,
        'title': 'TAX INVOICE',
        'hsn_rows': hsn_summary(lines),
        'amount_words': amount_in_words(invoice.grand_total),
        'tax_words': amount_in_words(invoice.tax_total),
        'pos_label': state_label(invoice.place_of_supply_state),
        'bill_state': state_label(invoice.bill_to_state_code),
        'ship_state': state_label(invoice.ship_to_state_code),
        'seller_state': state_label(invoice.seller_state_code),
        'seller_state_name': state_name(invoice.seller_state_code),
        'extra': invoice.seller_extra or {},
        'copies': _copies(copies),
        'for_pdf': for_pdf,
        'is_b2b_order': invoice.b2b_order_id is not None,
        'order': invoice.order,
        'credit_notes': list(invoice.credit_notes.all()) if not for_pdf else [],
        'order_balance': _balance(invoice.order),
    }


def credit_note_context(cn, copies=None, for_pdf=False):
    invoice = cn.invoice
    lines = list(cn.lines.select_related('invoice_line'))
    rows = [{
        'line_no': cl.line_no, 'description': cl.invoice_line.description, 'hsn_code': cl.invoice_line.hsn_code,
        'kind': cl.invoice_line.kind,
        'quantity': cl.quantity, 'uqc': cl.invoice_line.uqc, 'unit_price': cl.invoice_line.unit_price,
        'taxable_value': cl.taxable_value, 'gst_rate': cl.invoice_line.gst_rate,
        'cgst_amount': cl.cgst_amount, 'sgst_amount': cl.sgst_amount, 'igst_amount': cl.igst_amount,
        'cess_amount': cl.cess_amount, 'total_amount': cl.total_amount, 'tax_amount': cl.tax_amount,
        'is_free': cl.invoice_line.is_free, 'restock': cl.restock,
    } for cl in lines]

    class _Row(dict):
        __getattr__ = dict.get

    rows = [_Row(r) for r in rows]
    return {
        'doc': cn, 'invoice': invoice, 'lines': rows, 'is_credit_note': True,
        'title': 'CREDIT NOTE',
        'hsn_rows': hsn_summary(rows),
        'amount_words': amount_in_words(cn.grand_total),
        'tax_words': amount_in_words(cn.tax_total),
        'pos_label': state_label(invoice.place_of_supply_state),
        'bill_state': state_label(invoice.bill_to_state_code),
        'ship_state': state_label(invoice.ship_to_state_code),
        'seller_state': state_label(invoice.seller_state_code),
        'seller_state_name': state_name(invoice.seller_state_code),
        'extra': invoice.seller_extra or {},
        'copies': _copies(copies),
        'for_pdf': for_pdf,
        'is_b2b_order': invoice.b2b_order_id is not None,
        'order': invoice.order,
        'credit_notes': [],
    }


def _balance(order):
    if order is None or not hasattr(order, 'paid_amount'):
        return None
    return order.total_amount - order.paid_amount


def _write_pdf(html):
    from weasyprint import HTML

    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()


def render_invoice_pdf(invoice):
    return _write_pdf(render_to_string('billing/tax_document.html', invoice_context(invoice, for_pdf=True)))


def render_credit_note_pdf(cn):
    return _write_pdf(render_to_string('billing/tax_document.html', credit_note_context(cn, for_pdf=True)))


def render_html_pdf(template, context):
    """On-demand PDF of any template (Order Summary)."""
    return _write_pdf(render_to_string(template, context))
