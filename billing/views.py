"""
Admin side of GST billing: Tax Invoice / Credit Note print + stored PDF,
B2C credit notes and cancel & reissue, GST settings, and the read-only GST
records page (CA login).
"""
import csv
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from billing import pdf
from billing import services as billing
from billing.gst_states import STATES, normalise_state_code, state_label
from billing.gstin import validate_gstin
from billing.models import CreditNote, HSNCode, HSNRate, Invoice, InvoiceSeries, LegalEntity
from billing.tax import hsn_summary
from core.api import HasModulePermission, current_brand_id, err, has_module_permission, ok
from core.auth import admin_login_required, require_module
from core.models import Setting


# ── access helpers ───────────────────────────────────────────────────────

def _can_view_document(request, invoice):
    """GST records users see every document of the entity; order users see
    their own brand's documents for the module the order belongs to."""
    brand_id = current_brand_id(request)
    user = request.user
    if has_module_permission(user, brand_id, 'gst_records'):
        return True
    module = 'b2b' if invoice.b2b_order_id else 'orders'
    return invoice.brand_id == brand_id and has_module_permission(user, brand_id, module)


def _selected_copies(request):
    copies = [c for c in request.GET.getlist('copies') if c in billing.COPY_LABELS]
    return copies or billing.default_copies()


def _serve_pdf(doc, kind, filename, inline=True):
    if not doc.pdf_file:
        doc = billing.store_pdf(doc.pk, kind)
    response = FileResponse(doc.pdf_file.open('rb'), content_type='application/pdf')
    disposition = 'inline' if inline else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    response['X-Robots-Tag'] = 'noindex'
    response['Cache-Control'] = 'private, no-store'
    return response


def pdf_filename(doc):
    return doc.number.replace('/', '-') + '.pdf'


# ── print / PDF pages ────────────────────────────────────────────────────

@admin_login_required
def invoice_print(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related('b2c_order', 'b2b_order'), pk=pk)
    if not _can_view_document(request, invoice):
        raise PermissionDenied
    ctx = pdf.invoice_context(invoice, copies=_selected_copies(request))
    ctx.update({
        'copy_options': list(billing.COPY_LABELS.items()),
        'selected_copies': _selected_copies(request),
        'pdf_url': f'/admin/gst/invoice/{invoice.pk}/pdf/',
    })
    return render(request, 'billing/tax_document.html', ctx)


@admin_login_required
def invoice_pdf(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    if not _can_view_document(request, invoice):
        raise PermissionDenied
    return _serve_pdf(invoice, 'invoice', pdf_filename(invoice))


@admin_login_required
def credit_note_print(request, pk):
    cn = get_object_or_404(CreditNote.objects.select_related('invoice'), pk=pk)
    if not _can_view_document(request, cn.invoice):
        raise PermissionDenied
    ctx = pdf.credit_note_context(cn, copies=_selected_copies(request))
    ctx.update({
        'copy_options': list(billing.COPY_LABELS.items()),
        'selected_copies': _selected_copies(request),
        'pdf_url': f'/admin/gst/credit-note/{cn.pk}/pdf/',
    })
    return render(request, 'billing/tax_document.html', ctx)


@admin_login_required
def credit_note_pdf(request, pk):
    cn = get_object_or_404(CreditNote.objects.select_related('invoice'), pk=pk)
    if not _can_view_document(request, cn.invoice):
        raise PermissionDenied
    return _serve_pdf(cn, 'credit_note', pdf_filename(cn))


# ── B2C order billing API (credit notes, cancel & reissue) ───────────────

def _b2c_order(request, order_id):
    from orders.models import Order

    return Order.objects.filter(id=order_id or '', brand_id=current_brand_id(request)).first()


def b2c_billing_payload(order):
    invoice = billing.active_invoice(order)
    invoices = list(order.invoices.order_by('id'))
    credit_notes = [cn for inv in invoices for cn in inv.credit_notes.order_by('id')]
    return {
        'gst_enabled': bool(order.price_mode) or invoice is not None,
        'invoice': billing.invoice_payload(invoice),
        'invoice_lines': billing.invoice_lines_payload(invoice) if invoice else [],
        'cancelled_invoices': [
            {'id': i.id, 'number': i.number, 'date': i.invoice_date.isoformat()}
            for i in invoices if i.status == Invoice.Status.CANCELLED
        ],
        'credit_notes': [billing.credit_note_payload(cn) for cn in credit_notes],
        'reasons': CreditNote.Reason.choices,
    }


def _selections(invoice, raw):
    """[{line_id, quantity, restock}] -> [(InvoiceLine, qty, restock)]"""
    lines = {ln.id: ln for ln in invoice.lines.all()}
    out = []
    for row in raw or []:
        try:
            line = lines.get(int(row.get('line_id')))
            qty = int(row.get('quantity') or 0)
        except (TypeError, ValueError):
            continue
        if line is None or qty <= 0:
            continue
        out.append((line, qty, bool(row.get('restock')) and line.kind == 'GOODS'))
    return out


class B2COrderBillingAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'orders'

    def get(self, request):
        order = _b2c_order(request, request.query_params.get('order_id'))
        if not order:
            return err('Order not found.')
        return ok(b2c_billing_payload(order))

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        order = _b2c_order(request, body.get('order_id'))
        if not order:
            return err('Order not found.')
        action = body.get('action')
        if action == 'credit_note_preview':
            return self._preview(order, body)
        if action == 'credit_note':
            return self._credit_note(request, order, body)
        if action == 'cancel_reissue':
            return self._cancel_reissue(request, order, body)
        return err('Unknown action.')

    def _preview(self, order, body):
        invoice = billing.active_invoice(order)
        if not invoice:
            return err('This order has no GST invoice.')
        selections = _selections(invoice, body.get('lines'))
        if not selections:
            return err('Select at least one line to credit.')
        try:
            totals = billing.preview_totals(billing.preview_credit_note(invoice, selections))
        except billing.BillingError as exc:
            return err(str(exc))
        return ok({k: float(v) for k, v in totals.items()})

    def _credit_note(self, request, order, body):
        invoice = billing.active_invoice(order)
        if not invoice:
            return err('This order has no GST invoice.')
        try:
            cn = billing.b2c_credit_note(
                order, _selections(invoice, body.get('lines')), body.get('reason') or 'sales_return',
                request.user, notes=(body.get('notes') or '').strip(),
            )
        except billing.BillingError as exc:
            return err(str(exc))
        order.refresh_from_db()
        message = f'Credit note {cn.number} issued for ₹{cn.grand_total:,.2f}.'
        if order.status == 'cancelled':
            message += ' The order is fully credited and has been cancelled.'
            if order.payment_status == 'refund_initiated':
                message += ' Refund the customer and mark the payment refunded.'
        return ok({'credit_note': billing.credit_note_payload(cn)}, message)

    def _cancel_reissue(self, request, order, body):
        name = (body.get('name') or '').strip()
        address = (body.get('address') or '').strip()
        pincode = (body.get('pincode') or '').strip()
        state_code = normalise_state_code(body.get('shipping_state_code'))
        if not (name and address and state_code):
            return err('Name, address and state are required.')

        def apply_corrections(locked):
            locked.name = name
            locked.address = address
            locked.pincode = pincode
            locked.shipping_state_code = state_code
            locked.save(update_fields=['name', 'address', 'pincode', 'shipping_state_code', 'updated_at'])

        try:
            cn, new_invoice = billing.cancel_and_reissue(order, request.user, apply_corrections)
        except billing.BillingError as exc:
            return err(str(exc))
        return ok({'invoice': billing.invoice_payload(new_invoice)},
                  f'Credit note {cn.number} issued and new invoice {new_invoice.number} created.')


# ── GST settings ─────────────────────────────────────────────────────────

ENTITY_FIELDS = [
    'legal_name', 'trade_name', 'gstin', 'pan', 'state_code', 'address', 'fssai_no', 'email', 'phone',
    'bank_name', 'bank_account_no', 'bank_ifsc', 'bank_branch', 'upi_id', 'jurisdiction', 'signatory',
    'b2b_terms',
]
GST_SETTING_KEYS = ['gst_price_mode_b2b', 'gst_price_mode_b2c', 'gst_effective_date', 'gst_invoice_copies']


@admin_login_required
@require_module('settings')
@ensure_csrf_cookie
def gst_settings_page(request):
    return render(request, 'admin/gst-settings.html', {'states': STATES})


class GSTSettingsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'settings'

    def get(self, request):
        from products.models import Flavor

        entity = billing.entity_for_brand(current_brand_id(request))
        today = billing.ist_today()
        hsns = []
        for h in HSNCode.objects.prefetch_related('rates').order_by('code'):
            current = billing.rate_for(h, today)
            hsns.append({
                'id': h.id, 'code': h.code, 'description': h.description, 'uqc': h.uqc,
                'current_rate': float(current.gst_rate) if current else None,
                'rates': [{
                    'id': r.id, 'effective_from': r.effective_from.isoformat(),
                    'effective_to': r.effective_to.isoformat() if r.effective_to else None,
                    'gst_rate': float(r.gst_rate), 'cess_rate': float(r.cess_rate),
                } for r in h.rates.all()],
            })
        return ok({
            'entity': {f: getattr(entity, f) for f in ENTITY_FIELDS} if entity else None,
            'entity_error': billing.entity_config_error(entity),
            'settings': {k: billing.get_setting(k) for k in GST_SETTING_KEYS},
            'hsn_codes': hsns,
            'uqc_choices': HSNCode.UQC.choices,
            'flavors': [{
                'id': f.id, 'name': f.name, 'is_active': f.is_active, 'hsn_id': f.hsn_id,
            } for f in Flavor.objects.order_by('-is_active', 'name')],
            'gst_active': billing.gst_active(current_brand_id(request)),
        })

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        handlers = {
            'save_entity': self._save_entity,
            'save_settings': self._save_settings,
            'save_hsn': self._save_hsn,
            'add_rate': self._add_rate,
            'delete_rate': self._delete_rate,
            'map_flavor': self._map_flavor,
        }
        handler = handlers.get(body.get('action'))
        if not handler:
            return err('Unknown action.')
        return handler(request, body)

    def _save_entity(self, request, body):
        data = body.get('entity') or {}
        gstin, gstin_err = validate_gstin(data.get('gstin'))
        if gstin_err:
            return err(f'GSTIN: {gstin_err}')
        state_code = normalise_state_code(data.get('state_code'))
        if gstin and gstin[:2] != state_code:
            return err('The state does not match the GSTIN (first two digits).')
        pan = (data.get('pan') or '').strip().upper()
        if gstin and pan and gstin[2:12] != pan:
            return err('The PAN does not match the GSTIN (characters 3–12).')
        entity = billing.entity_for_brand(current_brand_id(request)) or LegalEntity(legal_name='')
        for field in ENTITY_FIELDS:
            if field in data:
                setattr(entity, field, (str(data.get(field) or '')).strip())
        entity.gstin = gstin
        entity.pan = pan or (gstin[2:12] if gstin else '')
        entity.state_code = state_code or '32'
        if not entity.legal_name:
            return err('Legal name is required.')
        entity.save()
        from core.models import Brand

        Brand.objects.filter(legal_entity__isnull=True).update(legal_entity=entity)
        billing._activity(request.user, 'GST business details updated', entity.legal_name)
        return ok(None, 'Business GST details saved.')

    def _save_settings(self, request, body):
        data = body.get('settings') or {}
        values = {}
        for kind in ('b2b', 'b2c'):
            mode = (data.get(f'gst_price_mode_{kind}') or '').strip().lower()
            if mode not in ('inclusive', 'exclusive'):
                return err(f'Invalid {kind.upper()} price mode.')
            values[f'gst_price_mode_{kind}'] = mode
        if values['gst_price_mode_b2c'] == 'exclusive':
            return err('GST on top for B2C (exclusive) needs the storefront checkout changes planned for '
                       'Phase 2; keep B2C on inclusive for now.')
        eff = billing.parse_iso_date(data.get('gst_effective_date'))
        if not eff:
            return err('Enter a valid GST effective date.')
        values['gst_effective_date'] = eff.isoformat()
        copies = [c for c in (data.get('gst_invoice_copies') or []) if c in billing.COPY_LABELS]
        values['gst_invoice_copies'] = ','.join(copies or ['original'])
        for key, value in values.items():
            Setting.objects.update_or_create(
                setting_key=key, defaults={'setting_value': value, 'category': 'gst'},
            )
        billing._activity(request.user, 'GST settings updated', str(values))
        return ok(None, 'GST settings saved. Price modes apply to orders created from now on.')

    def _save_hsn(self, request, body):
        code = ''.join(ch for ch in str(body.get('code') or '') if ch.isdigit())
        if len(code) not in (4, 6, 8):
            return err('HSN code must be 4, 6 or 8 digits.')
        uqc = body.get('uqc') if body.get('uqc') in HSNCode.UQC.values else HSNCode.UQC.KGS
        hsn_id = body.get('id')
        if hsn_id:
            hsn = HSNCode.objects.filter(id=hsn_id).first()
            if not hsn:
                return err('HSN code not found.')
        else:
            if HSNCode.objects.filter(code=code).exists():
                return err('That HSN code already exists.')
            hsn = HSNCode()
        hsn.code = code
        hsn.description = (body.get('description') or '').strip()[:255]
        hsn.uqc = uqc
        hsn.save()
        return ok({'id': hsn.id}, 'HSN code saved.')

    def _add_rate(self, request, body):
        hsn = HSNCode.objects.filter(id=body.get('hsn_id')).first()
        start = billing.parse_iso_date(body.get('effective_from'))
        end = billing.parse_iso_date(body.get('effective_to'))
        try:
            rate = Decimal(str(body.get('gst_rate')))
            cess = Decimal(str(body.get('cess_rate') or 0))
        except (ArithmeticError, ValueError):
            return err('Enter a valid GST rate.')
        if not hsn or not start:
            return err('HSN and effective-from date are required.')
        if rate < 0 or rate > 40 or cess < 0:
            return err('GST rate must be between 0 and 40%.')
        if end and end < start:
            return err('Effective-to must be after effective-from.')
        overlap = HSNRate.objects.filter(hsn=hsn).filter(
            Q(effective_to__isnull=True) | Q(effective_to__gte=start),
        )
        if end:
            overlap = overlap.filter(effective_from__lte=end)
        if overlap.exists():
            return err('This period overlaps an existing rate. Set an end date on the old rate first '
                       '(delete it and add it again with an end date).')
        HSNRate.objects.create(hsn=hsn, effective_from=start, effective_to=end, gst_rate=rate, cess_rate=cess)
        billing._activity(request.user, f'GST rate {rate}% added for HSN {hsn.code}', f'from {start}')
        return ok(None, 'Rate added.')

    def _delete_rate(self, request, body):
        deleted, _ = HSNRate.objects.filter(id=body.get('id')).delete()
        return ok(None, 'Rate deleted.') if deleted else err('Rate not found.')

    def _map_flavor(self, request, body):
        from products.models import Flavor

        flavor = Flavor.objects.filter(id=body.get('flavor_id')).first()
        if not flavor:
            return err('Flavor not found.')
        hsn_id = body.get('hsn_id') or None
        if hsn_id and not HSNCode.objects.filter(id=hsn_id).exists():
            return err('HSN code not found.')
        flavor.hsn_id = hsn_id
        flavor.save(update_fields=['hsn', 'updated_at'])
        return ok(None, f'HSN updated for {flavor.name}.')


# ── GST records (CA login) ───────────────────────────────────────────────

@admin_login_required
@require_module('gst_records')
@ensure_csrf_cookie
def gst_records_page(request):
    return render(request, 'admin/gst-records.html')


def _period(params):
    """(from, to) dates from month=YYYY-MM, fy=2026-27, or from/to."""
    month = params.get('month')
    fy = params.get('fy')
    if month:
        try:
            year, mon = (int(x) for x in month.split('-'))
            start = date(year, mon, 1)
            end = (date(year + (mon == 12), mon % 12 + 1, 1)) - timedelta(days=1)
            return start, end
        except (ValueError, TypeError):
            pass
    if fy:
        try:
            start_year = int(fy[:4])
            return date(start_year, 4, 1), date(start_year + 1, 3, 31)
        except ValueError:
            pass
    return billing.parse_iso_date(params.get('from')), billing.parse_iso_date(params.get('to'))


def _filtered_invoices(params):
    qs = Invoice.objects.select_related('brand')
    start, end = _period(params)
    if start:
        qs = qs.filter(invoice_date__gte=start)
    if end:
        qs = qs.filter(invoice_date__lte=end)
    if params.get('supply_type') in ('B2B', 'B2C'):
        qs = qs.filter(supply_type=params['supply_type'])
    search = (params.get('search') or '').strip()
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(bill_to_gstin__icontains=search)
                       | Q(bill_to_name__icontains=search) | Q(b2c_order_id__icontains=search)
                       | Q(b2b_order_id__icontains=search))
    return qs.order_by('invoice_date', 'id')


def _filtered_credit_notes(params):
    qs = CreditNote.objects.select_related('invoice', 'brand')
    start, end = _period(params)
    if start:
        qs = qs.filter(credit_note_date__gte=start)
    if end:
        qs = qs.filter(credit_note_date__lte=end)
    if params.get('supply_type') in ('B2B', 'B2C'):
        qs = qs.filter(invoice__supply_type=params['supply_type'])
    search = (params.get('search') or '').strip()
    if search:
        qs = qs.filter(Q(number__icontains=search) | Q(invoice__number__icontains=search)
                       | Q(invoice__bill_to_gstin__icontains=search)
                       | Q(invoice__bill_to_name__icontains=search))
    return qs.order_by('credit_note_date', 'id')


def _invoice_row(i):
    return {
        'id': i.id, 'number': i.number, 'date': i.invoice_date.isoformat(), 'brand': i.brand_name,
        'order_id': i.order_id, 'customer': i.bill_to_name, 'gstin': i.bill_to_gstin,
        'supply_type': i.supply_type, 'place_of_supply': state_label(i.place_of_supply_state),
        'taxable': float(i.taxable_total), 'cgst': float(i.cgst_total), 'sgst': float(i.sgst_total),
        'igst': float(i.igst_total), 'total': float(i.grand_total), 'status': i.status,
        'print_url': f'/admin/gst/invoice/{i.id}/', 'pdf_url': f'/admin/gst/invoice/{i.id}/pdf/',
    }


def _credit_note_row(cn):
    inv = cn.invoice
    return {
        'id': cn.id, 'number': cn.number, 'date': cn.credit_note_date.isoformat(),
        'invoice_number': inv.number, 'invoice_date': inv.invoice_date.isoformat(),
        'customer': inv.bill_to_name, 'gstin': inv.bill_to_gstin, 'supply_type': inv.supply_type,
        'place_of_supply': state_label(inv.place_of_supply_state), 'reason': cn.get_reason_display(),
        'taxable': float(cn.taxable_total), 'cgst': float(cn.cgst_total), 'sgst': float(cn.sgst_total),
        'igst': float(cn.igst_total), 'total': float(cn.grand_total),
        'print_url': f'/admin/gst/credit-note/{cn.id}/', 'pdf_url': f'/admin/gst/credit-note/{cn.id}/pdf/',
    }


def _totals(qs, fields):
    agg = qs.aggregate(**{k: Sum(v) for k, v in fields.items()})
    return {k: float(v or 0) for k, v in agg.items()}


def number_gaps():
    """Missing numbers in each series (should always be empty)."""
    problems = []
    for series in InvoiceSeries.objects.all():
        model = Invoice if series.doc_type == InvoiceSeries.DocType.INVOICE else CreditNote
        used = set(model.objects.filter(series=series).values_list('number', flat=True))
        missing = [billing.format_number(series, n) for n in range(1, series.last_number + 1)
                   if billing.format_number(series, n) not in used]
        if missing:
            problems.append({'series': f'{series.get_doc_type_display()} {series.fiscal_year}',
                             'missing': missing[:20], 'count': len(missing)})
    return problems


def dispatched_without_invoice():
    """Dispatched orders on/after the GST effective date with no invoice."""
    from orders.models import B2BOrder, Order

    eff = billing.effective_date()
    if eff is None:
        return []
    since = Q(order_date__date__gte=eff) | Q(updated_at__date__gte=eff)
    rows = []
    for o in (Order.objects.filter(status__in=billing.B2C_INVOICE_STATUSES).filter(since)
              .filter(invoices__isnull=True).order_by('order_date')):
        rows.append({'kind': 'B2C', 'order_id': o.id, 'name': o.name, 'status': o.status,
                     'order_date': o.order_date.isoformat(), 'url': f'/admin/orders/{o.id}/'})
    for o in (B2BOrder.objects.filter(status__in=billing.B2B_INVOICE_STATUSES).filter(since)
              .filter(invoices__isnull=True).select_related('company').order_by('order_date')):
        rows.append({'kind': 'B2B', 'order_id': o.id, 'name': o.company.company_name, 'status': o.status,
                     'order_date': o.order_date.isoformat(), 'url': '/admin/b2b-orders/?order=' + o.id})
    return rows


def pending_credit_notes():
    from orders.models import B2BReturn

    return [{
        'return_id': r.id, 'order_id': r.b2b_order_id, 'company': r.b2b_order.company.company_name,
        'date': r.created_at.isoformat(), 'amount': float(r.return_amount),
        'url': '/admin/b2b-orders/?order=' + r.b2b_order_id,
    } for r in B2BReturn.objects.filter(needs_credit_note=True, credit_note__isnull=True)
        .select_related('b2b_order__company').order_by('created_at')]


class GSTRecordsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'gst_records'

    def get(self, request):
        params = request.query_params
        tab = params.get('tab', 'invoices')
        try:
            page = max(1, int(params.get('page', 1)))
        except ValueError:
            page = 1
        per_page = 50
        if tab == 'credit_notes':
            qs = _filtered_credit_notes(params)
            rows = [_credit_note_row(cn) for cn in Paginator(qs, per_page).get_page(page)]
            totals = _totals(qs, {'taxable': 'taxable_total', 'cgst': 'cgst_total', 'sgst': 'sgst_total',
                                  'igst': 'igst_total', 'total': 'grand_total'})
            count = qs.count()
        elif tab == 'pending':
            rows, totals = pending_credit_notes(), {}
            count = len(rows)
        elif tab == 'missing':
            rows, totals = dispatched_without_invoice(), {}
            count = len(rows)
        else:
            qs = _filtered_invoices(params)
            rows = [_invoice_row(i) for i in Paginator(qs, per_page).get_page(page)]
            totals = _totals(qs, {'taxable': 'taxable_total', 'cgst': 'cgst_total', 'sgst': 'sgst_total',
                                  'igst': 'igst_total', 'total': 'grand_total'})
            count = qs.count()
        return ok({
            'rows': rows, 'totals': totals, 'count': count, 'page': page, 'per_page': per_page,
            'gaps': number_gaps(),
            'pending_count': len(pending_credit_notes()),
            'missing_count': len(dispatched_without_invoice()),
        })


def _csv(rows, header):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode('utf-8-sig')


@admin_login_required
@require_module('gst_records')
def gst_records_export(request):
    """ZIP of the stored PDFs + CSV registers for the selected period."""
    invoices = list(_filtered_invoices(request.GET).prefetch_related('lines'))
    credit_notes = list(_filtered_credit_notes(request.GET).prefetch_related('lines__invoice_line'))
    if len(invoices) + len(credit_notes) > 3000:
        return HttpResponse('Too many documents for one export — pick a shorter period.', status=400)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('invoices.csv', _csv([[
            i.number, i.invoice_date.isoformat(), i.supply_type, i.bill_to_name, i.bill_to_gstin,
            state_label(i.place_of_supply_state), i.order_id, i.taxable_total, i.cgst_total, i.sgst_total,
            i.igst_total, i.cess_total, i.grand_total, i.status, i.brand_name,
        ] for i in invoices], [
            'Invoice No', 'Date', 'Type', 'Customer', 'GSTIN', 'Place of Supply', 'Order', 'Taxable',
            'CGST', 'SGST', 'IGST', 'Cess', 'Total', 'Status', 'Brand',
        ]))
        zf.writestr('credit_notes.csv', _csv([[
            cn.number, cn.credit_note_date.isoformat(), cn.invoice.number, cn.invoice.invoice_date.isoformat(),
            cn.invoice.supply_type, cn.invoice.bill_to_name, cn.invoice.bill_to_gstin,
            state_label(cn.invoice.place_of_supply_state), cn.get_reason_display(), cn.taxable_total,
            cn.cgst_total, cn.sgst_total, cn.igst_total, cn.cess_total, cn.grand_total,
        ] for cn in credit_notes], [
            'Credit Note No', 'Date', 'Invoice No', 'Invoice Date', 'Type', 'Customer', 'GSTIN',
            'Place of Supply', 'Reason', 'Taxable', 'CGST', 'SGST', 'IGST', 'Cess', 'Total',
        ]))
        inv_lines = [ln for i in invoices for ln in i.lines.all()]
        cn_lines = [cl for cn in credit_notes for cl in cn.lines.all()]
        for cl in cn_lines:
            cl.hsn_code = cl.invoice_line.hsn_code
            cl.gst_rate = cl.invoice_line.gst_rate
            cl.uqc = cl.invoice_line.uqc
            cl.kind = cl.invoice_line.kind
        hsn_rows = []
        for label, lines in (('Invoices', inv_lines), ('Credit notes', cn_lines)):
            for row in hsn_summary(lines):
                hsn_rows.append([label, row['hsn_code'], row['uqc'], row['quantity'], row['gst_rate'],
                                 row['taxable_value'], row['cgst_amount'], row['sgst_amount'],
                                 row['igst_amount'], row['cess_amount'], row['total_amount']])
        zf.writestr('hsn_summary.csv', _csv(hsn_rows, [
            'Section', 'HSN', 'UQC', 'Quantity', 'Rate', 'Taxable', 'CGST', 'SGST', 'IGST', 'Cess', 'Total',
        ]))
        for doc, kind, folder in [(i, 'invoice', 'invoices') for i in invoices] + \
                                 [(cn, 'credit_note', 'credit_notes') for cn in credit_notes]:
            try:
                stored = doc if doc.pdf_file else billing.store_pdf(doc.pk, kind)
                with stored.pdf_file.open('rb') as fh:
                    zf.writestr(f'{folder}/{pdf_filename(doc)}', fh.read())
            except Exception as exc:  # noqa: BLE001 — note it in the archive, keep exporting
                zf.writestr(f'{folder}/{pdf_filename(doc)}.ERROR.txt', str(exc))
    start, end = _period(request.GET)
    label = f'{start or "all"}_{end or "all"}'
    response = HttpResponse(buf.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="gst-records_{label}.zip"'
    return response

