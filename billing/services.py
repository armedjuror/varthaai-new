"""
GST billing services: settings, numbering, building tax inputs from orders,
issuing invoices and credit notes, the freeze rule, and Order Summary data.

Everything that writes an Invoice / CreditNote goes through here so the
numbering lock, the snapshots and the activity log stay in one place.
"""
import hashlib
import logging
from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_date

from billing.gst_states import state_label, state_name
from billing.gstin import state_code_from_gstin, validate_gstin
from billing.models import (
    CreditNote, CreditNoteLine, HSNRate, Invoice, InvoiceLine, InvoiceSeries, LegalEntity,
)
from billing.tax import (
    CREDIT_FIELDS, EXCLUSIVE, INCLUSIVE, PRICE_MODES, ZERO, LineInput, compute_invoice,
    credit_line_amounts, q2,
)

log = logging.getLogger(__name__)

IST = ZoneInfo('Asia/Kolkata')

B2C_INVOICE_STATUSES = ('shipped', 'delivered')
B2B_INVOICE_STATUSES = ('dispatched', 'delivered')
EWAY_BILL_THRESHOLD = Decimal('50000')

SETTING_DEFAULTS = {
    'gst_price_mode_b2b': EXCLUSIVE,
    'gst_price_mode_b2c': INCLUSIVE,
    'gst_effective_date': '2026-10-01',
    'gst_invoice_copies': 'original,duplicate,triplicate',
}
COPY_LABELS = {
    'original': 'Original for Recipient',
    'duplicate': 'Duplicate for Transporter',
    'triplicate': 'Triplicate for Supplier',
}


class BillingError(Exception):
    """A user-facing reason why an invoice / credit note cannot be issued."""


# ── dates ────────────────────────────────────────────────────────────────

def ist_today():
    """Invoice dates use the IST calendar date (settings.TIME_ZONE is UTC)."""
    return timezone.now().astimezone(IST).date()


def ist_date(dt):
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt.astimezone(IST).date()


def fiscal_year(d):
    """Indian financial year (Apr–Mar) as '2026-27'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f'{start}-{(start + 1) % 100:02d}'


def format_number(series, n):
    short_fy = series.fiscal_year[2:]  # '2026-27' -> '26-27'
    return f'{series.prefix}{short_fy}/{n:0{series.pad_width}d}'


# ── settings / entity ────────────────────────────────────────────────────

def get_setting(key):
    from core.models import Setting

    value = Setting.objects.filter(setting_key=key).values_list('setting_value', flat=True).first()
    if value in (None, ''):
        return SETTING_DEFAULTS.get(key, '')
    return value


def price_mode_setting(kind):
    """kind: 'b2b' or 'b2c'."""
    mode = (get_setting(f'gst_price_mode_{kind}') or '').strip().lower()
    return mode if mode in PRICE_MODES else SETTING_DEFAULTS[f'gst_price_mode_{kind}']


def effective_date():
    raw = get_setting('gst_effective_date')
    try:
        return parse_date(raw) if raw else None
    except ValueError:
        return None


def default_copies():
    raw = get_setting('gst_invoice_copies') or ''
    copies = [c.strip() for c in raw.split(',') if c.strip() in COPY_LABELS]
    return copies or ['original']


def entity_for_brand(brand_id):
    from core.models import Brand

    brand = Brand.objects.filter(id=brand_id).select_related('legal_entity').first()
    if brand and brand.legal_entity:
        return brand.legal_entity
    return LegalEntity.objects.order_by('id').first()


def entity_config_error(entity):
    if entity is None:
        return 'No GST legal entity is set up. Fill in Settings → GST.'
    missing = [label for label, value in (
        ('GSTIN', entity.gstin), ('legal name', entity.legal_name), ('address', entity.address),
    ) if not value]
    if missing:
        return f'GST details are incomplete ({", ".join(missing)}). Fill them in Settings → GST.'
    _, gstin_err = validate_gstin(entity.gstin)
    if gstin_err:
        return f'Business GSTIN is invalid: {gstin_err}'
    return None


def gst_active(brand_id, on_date=None):
    """True when orders dispatched on `on_date` must get a GST invoice: the
    effective date has arrived and the brand bills under a legal entity.
    (An incomplete entity still counts as active, so dispatch is blocked
    with a clear message rather than silently skipping the invoice.)"""
    eff = effective_date()
    if eff is None:
        return False
    on_date = on_date or ist_today()
    return on_date >= eff and entity_for_brand(brand_id) is not None


# ── numbering ────────────────────────────────────────────────────────────

def next_number(entity, on_date, doc_type):
    """Allocate the next number in the (entity, FY, type) series. Must be
    called inside transaction.atomic(): the series row is locked until the
    transaction ends, so a rollback gives the number back (no gaps)."""
    fy = fiscal_year(on_date)
    InvoiceSeries.objects.get_or_create(
        entity=entity, fiscal_year=fy, doc_type=doc_type,
        defaults={
            'prefix': '' if doc_type == InvoiceSeries.DocType.INVOICE else 'CN/',
            'pad_width': 7 if doc_type == InvoiceSeries.DocType.INVOICE else 4,
        },
    )
    series = InvoiceSeries.objects.select_for_update().get(entity=entity, fiscal_year=fy, doc_type=doc_type)
    series.last_number += 1
    series.save(update_fields=['last_number'])
    return series, format_number(series, series.last_number)


# ── HSN rates ────────────────────────────────────────────────────────────

def rate_for(hsn, on_date):
    if hsn is None:
        return None
    return (
        HSNRate.objects.filter(hsn=hsn, effective_from__lte=on_date)
        .exclude(effective_to__lt=on_date)
        .order_by('-effective_from')
        .first()
    )


def _flavor_tax(flavor, name, on_date):
    """(hsn_code, uqc, rate, cess) for a flavor, or raise BillingError."""
    hsn = getattr(flavor, 'hsn', None) if flavor else None
    if hsn is None:
        raise BillingError(f'No HSN code is set for "{name}". Map it in Settings → GST.')
    rate = rate_for(hsn, on_date)
    if rate is None:
        raise BillingError(f'No GST rate for HSN {hsn.code} on {on_date:%d-%m-%Y}. Add it in Settings → GST.')
    return hsn.code, hsn.uqc, rate.gst_rate, rate.cess_rate


# ── B2C order -> tax inputs ──────────────────────────────────────────────

def b2c_lines(order, on_date):
    lines = []
    for it in order.items.select_related('flavor__hsn', 'flavor_pack').order_by('id'):
        hsn_code, uqc, rate, cess = _flavor_tax(it.flavor, it.flavor_name, on_date)
        grams = it.quantity
        if uqc == 'KGS' or not it.flavor_pack_id or not it.flavor_pack:
            qty = Decimal(grams) / 1000
            uqc = 'KGS' if uqc != 'KGS' and not it.flavor_pack_id else uqc
        else:
            qty = Decimal(grams) / Decimal(it.flavor_pack.weight_grams)
        desc = it.flavor_name + (f' ({it.pack_label})' if it.pack_label else '')
        lines.append(LineInput(
            description=desc, gross=q2(Decimal(str(it.sale_price_per_kg)) * grams / 1000),
            rate=rate, cess_rate=cess, hsn_code=hsn_code, uqc=uqc, quantity=qty,
            source_quantity=grams, ref=it,
        ))
    return lines


def b2c_tax(order, on_date=None, interstate=None):
    on_date = on_date or ist_today()
    entity = entity_for_brand(order.brand_id)
    lines = b2c_lines(order, on_date)
    if not lines:
        raise BillingError('The order has no items.')
    if interstate is None:
        pos = order.shipping_state_code
        interstate = bool(pos) and entity is not None and pos != entity.state_code
    goods = sum((ln.gross for ln in lines), ZERO)
    discount = min(q2(order.coupon_discount or 0), goods)
    return compute_invoice(
        lines, price_mode=order.price_mode or INCLUSIVE, interstate=interstate,
        discount=discount, shipping=q2(order.delivery_charge or 0),
    )


def refresh_b2c_gst(order):
    """Pin price mode (at creation) and refresh the GST estimate on a B2C
    order that has no invoice yet. Never blocks: a missing HSN just leaves
    the estimate at 0 until the order is dispatched."""
    if active_invoice(order):
        return
    fields = []
    if not order.price_mode and gst_active(order.brand_id):
        order.price_mode = price_mode_setting('b2c')
        fields.append('price_mode')
    gst = ZERO
    if order.price_mode:
        try:
            gst = b2c_tax(order).tax_total
        except (BillingError, ValueError):
            gst = ZERO
    if order.gst_amount != gst:
        order.gst_amount = gst
        fields.append('gst_amount')
    if fields:
        type(order).objects.filter(pk=order.pk).update(**{f: getattr(order, f) for f in fields})


# ── B2B order -> tax inputs ──────────────────────────────────────────────

def b2b_bill_to(company):
    gstin, gstin_err = validate_gstin(company.gst_number)
    if gstin_err:
        gstin = ''
    state = state_code_from_gstin(gstin) if gstin else (company.state_code or '')
    address = '\n'.join(p for p in (
        company.address, ', '.join(p for p in (company.city, state_name(state) or company.state) if p),
        company.pincode,
    ) if p)
    return {
        'name': (company.gst_legal_name or company.company_name) if gstin else company.company_name,
        'gstin': gstin, 'address': address, 'state_code': state, 'pincode': company.pincode,
    }


def b2b_place_of_supply(order, company=None):
    """Registered buyer: the bill-to GSTIN's state (IGST Act s.10(1)(b)).
    Unregistered: where the goods are delivered."""
    company = company or order.company
    bill_to = b2b_bill_to(company)
    if bill_to['gstin']:
        return bill_to['state_code']
    if not order.ship_to_same_as_bill_to and order.ship_to_state_code:
        return order.ship_to_state_code
    return bill_to['state_code']


def b2b_lines(order, on_date, items=None):
    lines = []
    items = items if items is not None else order.items.select_related('flavor__hsn').order_by('id')
    for it in items:
        net_qty = it.quantity - (getattr(it, 'returned_quantity', 0) or 0)
        if net_qty <= 0:
            continue
        hsn_code, uqc, rate, cess = _flavor_tax(it.flavor, it.flavor_name, on_date)
        if uqc == 'KGS':
            qty = Decimal(net_qty * it.weight_grams) / 1000
        else:
            qty = Decimal(net_qty)
        desc = it.flavor_name + (f' ({it.pack_label})' if it.pack_label else f' ({it.weight_grams}g)')
        lines.append(LineInput(
            description=desc, gross=ZERO if it.is_free_item else q2(it.selling_price * net_qty),
            rate=rate, cess_rate=cess, hsn_code=hsn_code, uqc=uqc, quantity=qty,
            source_quantity=net_qty, is_free=bool(it.is_free_item), ref=it,
        ))
    return lines


def b2b_compute(lines, *, price_mode, interstate, discount):
    goods = sum((ln.gross for ln in lines if not ln.is_free), ZERO)
    return compute_invoice(lines, price_mode=price_mode, interstate=interstate,
                           discount=min(max(q2(discount), ZERO), goods))


def b2b_tax(order, on_date=None):
    on_date = on_date or ist_today()
    entity = entity_for_brand(order.brand_id)
    lines = b2b_lines(order, on_date)
    if not lines:
        raise BillingError('The order has no items left to invoice.')
    mode = order.price_mode or INCLUSIVE
    pos = b2b_place_of_supply(order)
    interstate = bool(pos) and entity is not None and pos != entity.state_code
    goods = sum((ln.gross for ln in lines if not ln.is_free), ZERO)
    if mode == INCLUSIVE:
        # Invoice exactly what the buyer owes; anything the order total does
        # not cover (discounts, legacy returns) is the discount.
        discount = goods - q2(order.total_amount)
    else:
        discount = q2(order.discount_amount)
    return b2b_compute(lines, price_mode=mode, interstate=interstate, discount=discount)


# ── active invoice / freeze rule ─────────────────────────────────────────

def _order_kind(order):
    from orders.models import B2BOrder, Order

    if isinstance(order, Order):
        return 'b2c'
    if isinstance(order, B2BOrder):
        return 'b2b'
    raise TypeError(order)


def _order_filter(order):
    return {'b2c_order': order} if _order_kind(order) == 'b2c' else {'b2b_order': order}


def active_invoice(order):
    if order is None or order.pk is None:
        return None
    return Invoice.objects.filter(status=Invoice.Status.ACTIVE, **_order_filter(order)).first()


def freeze_error(order, action='edit'):
    inv = active_invoice(order)
    if inv is None:
        return None
    return (
        f'This order has GST invoice {inv.number}, so it cannot be {action}ed. '
        'Use a credit note (return) or Cancel & reissue instead.'
    )


def status_change_error(order, target):
    """Block moving an invoiced order back before dispatch / to cancelled."""
    inv = active_invoice(order)
    if inv is None:
        return None
    allowed = B2C_INVOICE_STATUSES if _order_kind(order) == 'b2c' else B2B_INVOICE_STATUSES
    if target in allowed:
        return None
    return (
        f'Order has GST invoice {inv.number}: it can only be {" or ".join(allowed)}. '
        'To cancel it, issue a credit note for the full order.'
    )


def needs_invoice(order, target_status, on_date=None):
    allowed = B2C_INVOICE_STATUSES if _order_kind(order) == 'b2c' else B2B_INVOICE_STATUSES
    return target_status in allowed and gst_active(order.brand_id, on_date)


# ── issue invoice ────────────────────────────────────────────────────────

def _activity(user, action, details, module='billing'):
    from core.models import ActivityLog

    ActivityLog.objects.create(admin=user if getattr(user, 'pk', None) else None,
                               action=action, module=module, details=details)


def _seller_snapshot(entity, brand):
    return {
        'seller_legal_name': entity.legal_name,
        'seller_trade_name': entity.trade_name,
        'seller_gstin': entity.gstin,
        'seller_address': entity.address,
        'seller_state_code': entity.state_code,
        'seller_fssai': entity.fssai_no,
        'seller_phone': entity.phone,
        'seller_email': entity.email,
        'seller_extra': {
            'pan': entity.pan,
            'bank_name': entity.bank_name, 'bank_account_no': entity.bank_account_no,
            'bank_ifsc': entity.bank_ifsc, 'bank_branch': entity.bank_branch, 'upi_id': entity.upi_id,
            'jurisdiction': entity.jurisdiction, 'signatory': entity.signatory,
            'b2b_terms': entity.b2b_terms,
        },
        'brand_name': brand.name if brand else '',
    }


def _b2c_parties(order):
    pos = order.shipping_state_code
    if not pos:
        raise BillingError(
            f'Order {order.id} has no delivery state. Set the state on the order before dispatch '
            '(needed for the GST invoice).'
        )
    return {
        'bill_to_name': order.name, 'bill_to_gstin': '', 'bill_to_address': order.address,
        'bill_to_state_code': pos, 'bill_to_pincode': order.pincode,
        'ship_to_same': True, 'place_of_supply_state': pos,
    }


def _b2b_parties(order):
    company = order.company
    bill_to = b2b_bill_to(company)
    pos = b2b_place_of_supply(order, company)
    if not pos:
        raise BillingError(
            f'No state for {company.company_name}. Add its GSTIN or state (or the delivery state) '
            'before dispatch — needed for the GST invoice.'
        )
    parties = {
        'bill_to_name': bill_to['name'], 'bill_to_gstin': bill_to['gstin'],
        'bill_to_address': bill_to['address'], 'bill_to_state_code': bill_to['state_code'] or pos,
        'bill_to_pincode': bill_to['pincode'],
        'ship_to_same': order.ship_to_same_as_bill_to, 'place_of_supply_state': pos,
        'customer_po_no': order.customer_po_no, 'customer_po_date': order.customer_po_date,
        'due_date': order.due_date,
    }
    if not order.ship_to_same_as_bill_to:
        parties.update({
            'ship_to_name': order.ship_to_name or company.company_name,
            'ship_to_address': order.ship_to_address,
            'ship_to_state_code': order.ship_to_state_code,
            'ship_to_pincode': order.ship_to_pincode,
            'ship_to_gstin': order.ship_to_gstin,
        })
    return parties


def _lock_order(order):
    return type(order).objects.select_for_update().get(pk=order.pk)


def ensure_invoice(order, user=None, on_date=None):
    """Return the order's active invoice, issuing it if there is none.
    Idempotent: the order row is locked, so double clicks and webhook
    retries produce one invoice."""
    with transaction.atomic():
        order = _lock_order(order)
        existing = active_invoice(order)
        if existing:
            return existing
        return _issue_invoice(order, user, on_date or ist_today())


def _issue_invoice(order, user, on_date, reissue_of=None):
    from core.models import Brand

    kind = _order_kind(order)
    entity = entity_for_brand(order.brand_id)
    config_err = entity_config_error(entity)
    if config_err:
        raise BillingError(config_err)

    if kind == 'b2c':
        parties = _b2c_parties(order)
        result = b2c_tax(order, on_date, interstate=parties['place_of_supply_state'] != entity.state_code)
    else:
        parties = _b2b_parties(order)
        result = b2b_tax(order, on_date)
    if not result.lines:
        raise BillingError('The order has no items to invoice.')

    brand = Brand.objects.filter(id=order.brand_id).first()
    series, number = next_number(entity, on_date, InvoiceSeries.DocType.INVOICE)
    invoice = Invoice.objects.create(
        entity=entity, brand_id=order.brand_id, series=series, number=number,
        fiscal_year=series.fiscal_year, invoice_date=on_date,
        b2c_order=order if kind == 'b2c' else None,
        b2b_order=order if kind == 'b2b' else None,
        reissue_of=reissue_of,
        supply_type=Invoice.SupplyType.B2B if parties.get('bill_to_gstin') else Invoice.SupplyType.B2C,
        price_mode=result.price_mode, is_interstate=result.is_interstate,
        gross_total=result.gross_total, discount_total=result.discount_total,
        taxable_total=result.taxable_total, cgst_total=result.cgst_total,
        sgst_total=result.sgst_total, igst_total=result.igst_total, cess_total=result.cess_total,
        grand_total=result.grand_total, created_by=user if getattr(user, 'pk', None) else None,
        **_seller_snapshot(entity, brand), **parties,
    )
    InvoiceLine.objects.bulk_create([
        InvoiceLine(
            invoice=invoice, line_no=i, kind=ln.kind,
            b2c_item=ln.ref if kind == 'b2c' and ln.ref is not None else None,
            b2b_item=ln.ref if kind == 'b2b' and ln.ref is not None else None,
            description=ln.description[:255], hsn_code=ln.hsn_code, uqc=ln.uqc,
            quantity=ln.quantity, source_quantity=ln.source_quantity, unit_price=ln.unit_price,
            gross_amount=ln.gross_amount, discount_amount=ln.discount_amount,
            taxable_value=ln.taxable_value, gst_rate=ln.gst_rate,
            cgst_amount=ln.cgst_amount, sgst_amount=ln.sgst_amount, igst_amount=ln.igst_amount,
            cess_amount=ln.cess_amount, total_amount=ln.total_amount, is_free=ln.is_free,
        )
        for i, ln in enumerate(result.lines, start=1)
    ])

    if kind == 'b2c':
        type(order).objects.filter(pk=order.pk).update(gst_amount=result.tax_total, price_mode=result.price_mode)
    else:
        _sync_b2b_totals_to_invoice(order, invoice)

    _activity(user, f'GST invoice {number} issued', f'{kind.upper()} order {order.pk}: ₹{invoice.grand_total}')
    transaction.on_commit(lambda: _safe_store_pdf(invoice.pk, 'invoice'))
    return invoice


def _recompute_payment_status(order):
    from orders.models import B2BOrder

    order.balance_amount = order.total_amount - order.paid_amount
    if order.total_amount > 0 and order.paid_amount >= order.total_amount:
        order.payment_status = B2BOrder.PaymentStatus.PAID
    elif order.paid_amount > 0:
        order.payment_status = B2BOrder.PaymentStatus.PARTIAL
    else:
        order.payment_status = B2BOrder.PaymentStatus.PENDING


def _sync_b2b_totals_to_invoice(order, invoice):
    """The tax invoice is the legal amount: an exclusive-mode order whose
    pinned GST estimate drifted (rate or state change) follows the invoice."""
    from finance.services import sync_b2b_order_income

    fields = {'invoice_number': invoice.number, 'gst_amount': invoice.tax_total,
              'price_mode': invoice.price_mode, 'place_of_supply_state': invoice.place_of_supply_state}
    for key, value in fields.items():
        setattr(order, key, value)
    update = list(fields)
    if invoice.price_mode == EXCLUSIVE and order.total_amount != invoice.grand_total:
        order.total_amount = invoice.grand_total
        _recompute_payment_status(order)
        update += ['total_amount', 'balance_amount', 'payment_status']
    order.save(update_fields=update + ['updated_at'])
    sync_b2b_order_income(order)


# ── credit notes ─────────────────────────────────────────────────────────

def credited_so_far(invoice_lines):
    """{invoice_line_id: {'source_quantity': n, <CREDIT_FIELDS>: sums}}"""
    rows = (
        CreditNoteLine.objects.filter(invoice_line__in=invoice_lines)
        .values('invoice_line_id')
        .annotate(source_quantity=Sum('source_quantity'),
                  **{f: Sum(f) for f in CREDIT_FIELDS})
    )
    return {r['invoice_line_id']: r for r in rows}


def remaining_source_quantity(line, credited=None):
    credited = credited if credited is not None else credited_so_far([line])
    return line.source_quantity - (credited.get(line.id, {}).get('source_quantity') or 0)


def is_fully_credited(invoice):
    lines = list(invoice.lines.all())
    credited = credited_so_far(lines)
    return all(remaining_source_quantity(ln, credited) <= 0 for ln in lines)


def preview_credit_note(invoice, selections):
    """selections: [(invoice_line, source_quantity, restock)] -> list of
    dicts with the amounts a credit note would carry (nothing saved)."""
    lines = [s[0] for s in selections]
    credited = credited_so_far(lines)
    out = []
    for line, source_qty, restock in selections:
        if line.invoice_id != invoice.id:
            raise BillingError('Line does not belong to this invoice.')
        earlier = credited.get(line.id, {})
        try:
            amounts = credit_line_amounts(
                line, earlier.get('source_quantity') or 0,
                {f: earlier.get(f) or ZERO for f in CREDIT_FIELDS}, source_qty,
            )
        except ValueError as exc:
            raise BillingError(f'{line.description}: {exc}') from exc
        out.append({'line': line, 'source_quantity': source_qty, 'restock': restock, **amounts})
    return out


def preview_totals(rows):
    totals = {f: sum((r[f] for r in rows), ZERO) for f in CREDIT_FIELDS}
    totals['grand_total'] = sum((r['total_amount'] for r in rows), ZERO)
    totals['tax_total'] = totals['grand_total'] - totals['taxable_value']
    return totals


def issue_credit_note(invoice, selections, reason, user=None, notes='', on_date=None):
    """Issue a numbered credit note for `selections` against `invoice`.
    Locks the invoice lines so concurrent credit notes cannot over-credit."""
    on_date = on_date or ist_today()
    if reason not in CreditNote.Reason.values:
        raise BillingError('Invalid credit note reason.')
    selections = [s for s in selections if s[1] > 0]
    if not selections:
        raise BillingError('Select at least one line to credit.')
    with transaction.atomic():
        list(InvoiceLine.objects.select_for_update().filter(invoice=invoice))
        rows = preview_credit_note(invoice, selections)
        totals = preview_totals(rows)
        series, number = next_number(invoice.entity, on_date, InvoiceSeries.DocType.CREDIT_NOTE)
        cn = CreditNote.objects.create(
            entity=invoice.entity, brand_id=invoice.brand_id, series=series, number=number,
            fiscal_year=series.fiscal_year, credit_note_date=on_date, invoice=invoice,
            reason=reason, notes=notes,
            taxable_total=totals['taxable_value'], cgst_total=totals['cgst_amount'],
            sgst_total=totals['sgst_amount'], igst_total=totals['igst_amount'],
            cess_total=totals['cess_amount'], grand_total=totals['grand_total'],
            created_by=user if getattr(user, 'pk', None) else None,
        )
        CreditNoteLine.objects.bulk_create([
            CreditNoteLine(
                credit_note=cn, invoice_line=r['line'], line_no=i,
                source_quantity=r['source_quantity'], quantity=r['quantity'],
                taxable_value=r['taxable_value'], cgst_amount=r['cgst_amount'],
                sgst_amount=r['sgst_amount'], igst_amount=r['igst_amount'],
                cess_amount=r['cess_amount'], total_amount=r['total_amount'], restock=bool(r['restock']),
            )
            for i, r in enumerate(rows, start=1)
        ])
        _activity(user, f'Credit note {number} issued',
                  f'Against invoice {invoice.number} (order {invoice.order_id}): ₹{cn.grand_total}')
        transaction.on_commit(lambda: _safe_store_pdf(cn.pk, 'credit_note'))
    return cn


def full_credit_selections(invoice, restock=False):
    lines = list(invoice.lines.all())
    credited = credited_so_far(lines)
    return [(ln, remaining_source_quantity(ln, credited), restock and ln.kind == 'GOODS') for ln in lines
            if remaining_source_quantity(ln, credited) > 0]


# ── B2C credit note (return / cancel after dispatch) ─────────────────────

def b2c_credit_note(order, selections, reason, user, notes=''):
    """Credit selected lines of a B2C order's invoice, restock chosen goods
    and, when everything is credited, cancel the order."""
    from finance.services import sync_b2c_order_income
    from products.services import revert_stock

    with transaction.atomic():
        order = _lock_order(order)
        invoice = active_invoice(order)
        if invoice is None:
            raise BillingError('This order has no GST invoice to credit.')
        cn = issue_credit_note(invoice, selections, reason, user, notes)
        for cl in cn.lines.select_related('invoice_line__b2c_item__flavor'):
            item = cl.invoice_line.b2c_item
            if cl.restock and order.stock_deducted and item and item.flavor:
                revert_stock(item.flavor, cl.source_quantity, reference_type='sale', reference_id=order.id,
                             created_by=user, notes=f'Returned — credit note {cn.number}')
        if is_fully_credited(invoice):
            order.status = 'cancelled'
            fields = ['status', 'updated_at']
            if order.payment_status == 'paid':
                order.payment_status = 'refund_initiated'
                fields.append('payment_status')
            order.save(update_fields=fields)
        sync_b2c_order_income(order)
    return cn


# ── cancel & reissue ─────────────────────────────────────────────────────

def cancel_and_reissue(order, user, apply_corrections):
    """Full credit note on the active invoice, then a new invoice from the
    corrected order. `apply_corrections(order)` writes the corrected
    GSTIN / state / address inside the same transaction."""
    with transaction.atomic():
        order = _lock_order(order)
        invoice = active_invoice(order)
        if invoice is None:
            raise BillingError('This order has no GST invoice.')
        if invoice.credit_notes.exists():
            raise BillingError(
                'This invoice already has credit notes, so it cannot be cancelled and reissued here. '
                'Issue a credit note for the rest instead.'
            )
        apply_corrections(order)
        order = _lock_order(order)
        cn = issue_credit_note(invoice, full_credit_selections(invoice), CreditNote.Reason.CORRECTION, user,
                               notes='Cancelled for reissue with corrected details.')
        Invoice.objects.filter(pk=invoice.pk).update(status=Invoice.Status.CANCELLED)
        new_invoice = _issue_invoice(order, user, ist_today(), reissue_of=invoice)
        _activity(user, f'Invoice {invoice.number} cancelled and reissued as {new_invoice.number}',
                  f'Credit note {cn.number}')
    return cn, new_invoice


def set_eway_bill(invoice, number, bill_date):
    Invoice.objects.filter(pk=invoice.pk).update(eway_bill_no=(number or '').strip()[:20], eway_bill_date=bill_date)


def eway_bill_required(is_interstate, total):
    return bool(is_interstate) and Decimal(total or 0) > EWAY_BILL_THRESHOLD


# ── PDFs (generated once, stored with their SHA-256) ─────────────────────

def _safe_store_pdf(pk, kind):
    try:
        store_pdf(pk, kind)
    except Exception:  # noqa: BLE001 — a PDF failure must never undo an issued invoice
        log.exception('Could not render %s %s to PDF; it will be generated on first download.', kind, pk)


def store_pdf(pk, kind):
    """Render and store the PDF if it does not exist yet. Returns the row."""
    from billing import pdf

    model = Invoice if kind == 'invoice' else CreditNote
    doc = model.objects.get(pk=pk)
    if doc.pdf_file:
        return doc
    data = pdf.render_invoice_pdf(doc) if kind == 'invoice' else pdf.render_credit_note_pdf(doc)
    name = doc.number.replace('/', '-') + '.pdf'
    field = model._meta.get_field('pdf_file')
    path = field.storage.save(field.generate_filename(doc, name), ContentFile(data))
    updated = model.objects.filter(pk=pk, pdf_file='').update(
        pdf_file=path, pdf_sha256=hashlib.sha256(data).hexdigest(),
    )
    if not updated:  # someone else stored it first — keep theirs
        field.storage.delete(path)
    return model.objects.get(pk=pk)


# ── Order Summary data ───────────────────────────────────────────────────

def order_gst_summary(order):
    """GST block for the Order Summary page (admin + storefront). Values come
    from the issued invoice when there is one, otherwise from the tax engine
    with the order's pinned price mode. Returns None for orders outside GST
    billing (created before it, never invoiced)."""
    invoices = list(Invoice.objects.filter(**_order_filter(order)).order_by('id'))
    invoice = next((i for i in invoices if i.status == Invoice.Status.ACTIVE), None)
    credit_notes = list(CreditNote.objects.filter(invoice__in=invoices).order_by('id'))
    if invoice is None and not order.price_mode:
        return None
    data = {
        'invoice': invoice, 'invoices': invoices, 'credit_notes': credit_notes,
        'credit_total': sum((cn.grand_total for cn in credit_notes if cn.invoice_id == getattr(invoice, 'id', None)),
                            ZERO),
    }
    if invoice is not None:
        data.update({
            'price_mode': invoice.price_mode, 'is_interstate': invoice.is_interstate,
            'cgst': invoice.cgst_total, 'sgst': invoice.sgst_total, 'igst': invoice.igst_total,
            'tax_total': invoice.tax_total, 'taxable_total': invoice.taxable_total,
            'grand_total': invoice.grand_total,
            'place_of_supply': state_label(invoice.place_of_supply_state),
        })
        return data
    try:
        if _order_kind(order) == 'b2c':
            result = b2c_tax(order)
            pos = order.shipping_state_code
        else:
            result = b2b_tax(order)
            pos = b2b_place_of_supply(order)
    except (BillingError, ValueError):
        data.update({'unavailable': True, 'price_mode': order.price_mode})
        return data
    data.update({
        'price_mode': result.price_mode, 'is_interstate': result.is_interstate,
        'cgst': result.cgst_total, 'sgst': result.sgst_total, 'igst': result.igst_total,
        'tax_total': result.tax_total, 'taxable_total': result.taxable_total,
        'grand_total': result.grand_total, 'place_of_supply': state_label(pos),
    })
    return data


def invoice_payload(invoice):
    """Small JSON shape for admin screens."""
    if invoice is None:
        return None
    return {
        'id': invoice.id, 'number': invoice.number, 'date': invoice.invoice_date.isoformat(),
        'grand_total': float(invoice.grand_total), 'tax_total': float(invoice.tax_total),
        'is_interstate': invoice.is_interstate, 'status': invoice.status,
        'place_of_supply': state_label(invoice.place_of_supply_state),
        'eway_bill_no': invoice.eway_bill_no,
        'eway_bill_date': invoice.eway_bill_date.isoformat() if invoice.eway_bill_date else None,
        'eway_required': eway_bill_required(invoice.is_interstate, invoice.grand_total),
        'has_credit_notes': invoice.credit_notes.exists(),
        'fully_credited': is_fully_credited(invoice),
    }


def credit_note_payload(cn):
    return {
        'id': cn.id, 'number': cn.number, 'date': cn.credit_note_date.isoformat(),
        'grand_total': float(cn.grand_total), 'reason': cn.get_reason_display(),
        'invoice_number': cn.invoice.number,
    }


def invoice_lines_payload(invoice):
    lines = list(invoice.lines.all())
    credited = credited_so_far(lines)
    return [{
        'id': ln.id, 'kind': ln.kind, 'description': ln.description, 'hsn_code': ln.hsn_code,
        'quantity': float(ln.quantity), 'uqc': ln.uqc, 'source_quantity': ln.source_quantity,
        'remaining_source_quantity': remaining_source_quantity(ln, credited),
        'total_amount': float(ln.total_amount), 'is_free': ln.is_free,
        'b2b_item_id': ln.b2b_item_id, 'b2c_item_id': ln.b2c_item_id,
    } for ln in lines]


def parse_iso_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return parse_date(str(value))
    except ValueError:
        return None



# ── B2B order totals before an invoice exists ────────────────────────────

def b2b_estimate(brand_id, company, items, discount, price_mode, ship_to_same=True, ship_to_state_code=''):
    """GST for a B2B order that is being created / edited (nothing saved).

    `items` are dicts as parsed by the order form (flavor_id, quantity,
    weight_grams, selling_price, is_free_item, flavor_name, pack_label).
    Returns (gst_amount, total_amount, TaxResult). Raises BillingError when a
    flavor has no HSN / rate."""
    from types import SimpleNamespace

    from products.models import Flavor

    flavors = Flavor.objects.select_related('hsn').in_bulk({it['flavor_id'] for it in items})
    on_date = ist_today()
    objs = [SimpleNamespace(
        quantity=it['quantity'], returned_quantity=0, weight_grams=it['weight_grams'],
        selling_price=it['selling_price'], is_free_item=it['is_free_item'],
        flavor=flavors.get(it['flavor_id']), flavor_name=it['flavor_name'], pack_label=it.get('pack_label'),
    ) for it in items]
    lines = b2b_lines(None, on_date, items=objs)
    entity = entity_for_brand(brand_id)
    pos = b2b_place_of_supply(SimpleNamespace(
        ship_to_same_as_bill_to=ship_to_same, ship_to_state_code=ship_to_state_code,
    ), company)
    interstate = bool(pos) and entity is not None and pos != entity.state_code
    result = b2b_compute(lines, price_mode=price_mode, interstate=interstate, discount=discount)
    return result.tax_total, result.grand_total, result


def tax_result_payload(result):
    return {
        'price_mode': result.price_mode, 'is_interstate': result.is_interstate,
        'taxable_total': float(result.taxable_total), 'cgst': float(result.cgst_total),
        'sgst': float(result.sgst_total), 'igst': float(result.igst_total),
        'tax_total': float(result.tax_total), 'grand_total': float(result.grand_total),
    }
