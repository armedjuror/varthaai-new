"""
B2B orders + offers slice (Phase 10).

Mirrors the PHP `admin/api/b2b-orders.php` and `admin/api/b2b-offers.php`
(GET `?view=` / POST-with-`action`) plus the order list, create/edit and
invoice pages. Everything is scoped to the active brand via `current_brand_id`.

Stock changes go through the shared `products.services` (never re-implemented
here). B2B balances are always computed from `total_amount - paid_amount`, never
the stored `balance_amount` column.
"""
from datetime import date, timedelta
from types import SimpleNamespace
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from billing import services as billing
from billing.gst_states import normalise_state_code, state_label
from billing.gstin import validate_gstin
from billing.models import Invoice
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from core.models import Brand, Setting
from crm.models import B2BActivity, B2BCompany, B2BContact
from finance.services import sync_b2b_order_income
from orders.models import B2BOffer, B2BOrder, B2BOrderItem, B2BPayment, B2BReturn
from products.models import Flavor, FlavorPack, Stock
from products.services import available_grams, deduct_from_batch, deduct_stock, restock_batch, revert_stock

OFFER_TYPES = ['buy_x_get_y', 'discount_percent', 'flat_discount']


# ── HTML pages ───────────────────────────────────────────────────────────────

@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def b2b_orders_page(request):
    return render(request, 'admin/b2b-orders.html')


@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def b2b_order_create_page(request):
    return render(request, 'admin/b2b-order-create.html', {
        'repeat_order_id': request.GET.get('repeat', ''),
        'edit_order_id': request.GET.get('edit', ''),
        'preselect_company': _int_or_none(request.GET.get('company')) or 0,
    })


@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def b2b_offers_page(request):
    return render(request, 'admin/b2b-offers.html')


@admin_login_required
@require_module('b2b')
def print_b2b_invoice_page(request, pk):
    brand_id = current_brand_id(request)
    order = (
        B2BOrder.objects
        .filter(id=pk, brand_id=brand_id)
        .select_related('company', 'contact')
        .first()
    )
    if not order:
        return redirect('orders:b2b_orders')

    items = list(order.items.order_by('id'))
    for it in items:
        it.line_total = Decimal('0') if it.is_free_item else it.selling_price * it.quantity
    payments = list(
        order.payments.select_related('created_by').order_by('-payment_date'),
    )
    total_weight = sum(it.total_weight_grams for it in items)
    settings_map = {
        s.setting_key: s.setting_value for s in Setting.objects.all()
    }
    brand = Brand.objects.filter(id=brand_id).first()

    return render(request, 'admin/print-b2b-invoice.html', {
        'order': order,
        'company': order.company,
        'contact': order.contact,
        'items': items,
        'payments': payments,
        'total_weight': total_weight,
        'total_weight_kg': total_weight / 1000,
        'balance': order.total_amount - order.paid_amount,
        'settings': settings_map,
        'brand': brand,
    })


# ── Serialisers (plain dicts; booleans as 1/0 for the ported jQuery) ──────────

def _order_row(o):
    """List row. Balance is always computed from total - paid."""
    items = list(o.items.all())
    flavor_ids = {it.flavor_id for it in items}
    packs_count = sum(it.quantity for it in items if it.flavor_pack_id)
    custom_count = sum(1 for it in items if not it.flavor_pack_id)
    return {
        'id': o.id,
        'company_id': o.company_id,
        'company_name': o.company.company_name,
        'flavors_count': len(flavor_ids),
        'packs_count': packs_count,
        'custom_count': custom_count,
        'subtotal': float(o.subtotal),
        'total_amount': float(o.total_amount),
        'paid_amount': float(o.paid_amount),
        'balance_amount': float(o.total_amount - o.paid_amount),
        'status': o.status,
        'payment_status': o.payment_status,
        'stock_deducted': 1 if o.stock_deducted else 0,
        'has_returnable': 1 if any(it.quantity > it.returned_quantity for it in items) else 0,
        'due_date': o.due_date.isoformat() if o.due_date else None,
        'order_date': o.order_date.isoformat(),
        'invoice_number': o.invoice_number,
        'credit_note_pending': 1 if any(r.credit_note_pending for r in o.returns.all()) else 0,
    }


def _order_detail(o):
    return {
        'id': o.id,
        'company_id': o.company_id,
        'company_name': o.company.company_name,
        'contact_name': o.contact.name if o.contact else None,
        'status': o.status,
        'payment_status': o.payment_status,
        'subtotal': float(o.subtotal),
        'discount_type': o.discount_type,
        'discount_value': float(o.discount_value),
        'discount_amount': float(o.discount_amount),
        'offer_discount_amount': float(o.offer_discount_amount),
        'total_amount': float(o.total_amount),
        'paid_amount': float(o.paid_amount),
        'balance_amount': float(o.total_amount - o.paid_amount),
        'due_date': o.due_date.isoformat() if o.due_date else None,
        'source_order_id': o.source_order_id,
        'notes': o.notes,
        'order_date': o.order_date.isoformat(),
        'price_mode': o.price_mode,
        'gst_amount': float(o.gst_amount),
        'ship_to_same_as_bill_to': 1 if o.ship_to_same_as_bill_to else 0,
        'ship_to_name': o.ship_to_name,
        'ship_to_address': o.ship_to_address,
        'ship_to_state_code': o.ship_to_state_code,
        'ship_to_pincode': o.ship_to_pincode,
        'ship_to_gstin': o.ship_to_gstin,
        'place_of_supply_state': o.place_of_supply_state,
        'place_of_supply': state_label(o.place_of_supply_state),
        'customer_po_no': o.customer_po_no,
        'customer_po_date': o.customer_po_date.isoformat() if o.customer_po_date else None,
        'company_gst_number': o.company.gst_number,
        'company_gst_legal_name': o.company.gst_legal_name,
        'company_address': o.company.address,
        'company_city': o.company.city,
        'company_state_code': o.company.state_code,
        'company_pincode': o.company.pincode,
    }


def _item_row(it):
    return {
        'id': it.id,
        'flavor_id': it.flavor_id,
        'flavor_pack_id': it.flavor_pack_id,
        'stock_id': it.stock_id,
        'batch_number': it.stock.batch_number if it.stock_id and it.stock else None,
        'quantity': it.quantity,
        'weight_grams': it.weight_grams,
        'total_weight_grams': it.total_weight_grams,
        'mrp': float(it.mrp) if it.mrp is not None else None,
        'selling_price': float(it.selling_price),
        'cost_price': float(it.cost_price) if it.cost_price is not None else None,
        'is_free_item': 1 if it.is_free_item else 0,
        'offer_id': it.offer_id,
        'flavor_name': it.flavor_name,
        'pack_label': it.pack_label,
        'returned_quantity': it.returned_quantity,
    }


def _payment_row(p):
    return {
        'id': p.id,
        'amount': float(p.amount),
        'payment_type': p.payment_type,
        'payment_method': p.payment_method,
        'reference_number': p.reference_number,
        'notes': p.notes,
        'payment_date': p.payment_date.isoformat() if p.payment_date else None,
        'created_by_name': p.created_by.name if p.created_by else '',
    }


def _return_row(r):
    return {
        'id': r.id,
        'items': r.items,
        'return_amount': float(r.return_amount),
        'refund_amount': float(r.refund_amount),
        'credit_note_pending': 1 if r.credit_note_pending else 0,
        'credit_note_id': r.credit_note_id,
        'credit_note_number': r.credit_note.number if r.credit_note_id else None,
        'notes': r.notes,
        'created_by_name': r.created_by.name if r.created_by else '',
        'created_at': r.created_at.isoformat(),
    }


def _offer_row(o):
    return {
        'id': o.id,
        'company_id': o.company_id,
        'company_name': o.company.company_name if o.company else None,
        'name': o.name,
        'description': o.description,
        'type': o.type,
        'min_quantity': o.min_quantity,
        'free_quantity': o.free_quantity,
        'discount_value': float(o.discount_value) if o.discount_value is not None else None,
        'is_stackable': 1 if o.is_stackable else 0,
        'valid_from': o.valid_from.isoformat() if o.valid_from else None,
        'valid_to': o.valid_to.isoformat() if o.valid_to else None,
        'is_active': 1 if o.is_active else 0,
    }


# ── B2B orders API ────────────────────────────────────────────────────────────

class B2BOrdersAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    # ── GET dispatch ──
    def get(self, request):
        brand_id = current_brand_id(request)
        view = request.query_params.get('view', 'orders')

        if view == 'company_balance':
            return self._company_balance(request, brand_id)
        if view == 'order_form_data':
            return self._order_form_data(brand_id)
        if view == 'order':
            return self._order_detail(request, brand_id)
        if view == 'offers':
            return self._applicable_offers(request, brand_id)
        return self._orders_list(request, brand_id)

    def _company_balance(self, request, brand_id):
        cid = _int_or_none(request.query_params.get('company_id'))
        if not cid:
            return err('Missing company_id')
        today = timezone.localdate()
        outstanding = Decimal('0')
        overdue = Decimal('0')
        advance = Decimal('0')
        rows = (
            B2BOrder.objects
            .filter(company_id=cid, brand_id=brand_id)
            .exclude(status='cancelled')
            .values_list('total_amount', 'paid_amount', 'due_date')
        )
        for total, paid, due in rows:
            bal = total - paid
            if bal > 0:
                outstanding += bal
                if due and due < today:
                    overdue += bal
            elif bal < 0:
                advance += -bal
        company = B2BCompany.objects.filter(id=cid, brand_id=brand_id).first()
        return ok({
            'outstanding': float(outstanding),
            'overdue': float(overdue),
            'advance': float(advance),
            'credit_limit': float(company.credit_limit) if company else 0,
        })

    def _order_form_data(self, brand_id):
        flavors = Flavor.objects.filter(
            is_active=True,
        ).order_by('name')
        packs = FlavorPack.objects.filter(
            brand_id=brand_id, is_active=True,
        ).order_by('flavor_id', 'weight_grams')
        batches = Stock.objects.filter(
            quantity_grams__gt=0,
        ).order_by('flavor_id', '-is_active_batch', 'last_restocked_date')
        companies = (
            B2BCompany.objects
            .filter(brand_id=brand_id, is_active=True)
            .prefetch_related(Prefetch(
                'contacts',
                queryset=B2BContact.objects.filter(is_active=True).order_by('name'),
                to_attr='active_contacts',
            ))
            .order_by('company_name')
        )
        return ok({
            'flavors': [{
                'id': f.id, 'name': f.name,
                'price_per_kg': f.price_per_kg,
                'sale_price_per_kg': f.sale_price_per_kg,
            } for f in flavors],
            'packs': [{
                'id': p.id, 'flavor_id': p.flavor_id,
                'weight_grams': p.weight_grams, 'label': p.label,
                'mrp': float(p.mrp), 'selling_price': float(p.selling_price),
                'cost_price': float(p.cost_price),
            } for p in packs],
            'batches': [{
                'id': b.id, 'flavor_id': b.flavor_id,
                'batch_number': b.batch_number,
                'quantity_grams': b.quantity_grams,
                'cost_price_per_kg': float(b.cost_price_per_kg) if b.cost_price_per_kg is not None else 0,
                'expiry_date': b.expiry_date.isoformat() if b.expiry_date else None,
                'is_active_batch': 1 if b.is_active_batch else 0,
            } for b in batches],
            'companies': [{
                'id': c.id, 'company_name': c.company_name,
                'stage': c.stage,
                'discount_type': c.discount_type,
                'discount_value': float(c.discount_value) if c.discount_value is not None else None,
                'payment_terms_days': c.payment_terms_days,
                'credit_limit': float(c.credit_limit),
                'contacts_raw': '|'.join(
                    f'{ct.id}:{ct.name}' for ct in c.active_contacts
                ),
            } for c in companies],
        })

    def _order_detail(self, request, brand_id):
        order = (
            B2BOrder.objects
            .filter(id=request.query_params.get('id'), brand_id=brand_id)
            .select_related('company', 'contact')
            .first()
        )
        if not order:
            return err('Order not found.')
        items = order.items.select_related('stock').order_by('id')
        payments = order.payments.select_related('created_by').order_by('-payment_date')
        returns = order.returns.select_related('created_by', 'credit_note').order_by('-created_at')
        return ok({
            'order': _order_detail(order),
            'items': [_item_row(it) for it in items],
            'payments': [_payment_row(p) for p in payments],
            'returns': [_return_row(r) for r in returns],
            'billing': _billing_payload(order),
        })

    def _applicable_offers(self, request, brand_id):
        cid = _int_or_none(request.query_params.get('company_id'))
        today = timezone.localdate()
        offers = (
            B2BOffer.objects
            .filter(brand_id=brand_id, is_active=True)
            .filter(Q(company_id__isnull=True) | Q(company_id=cid))
            .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
            .select_related('company')
            .order_by('type', 'name')
        )
        return ok([_offer_row(o) for o in offers])

    def _orders_list(self, request, brand_id):
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = min(100, max(10, int(request.query_params.get('per_page', 25))))
        except (TypeError, ValueError):
            per_page = 25

        status = request.query_params.get('status', '')
        payment_status = request.query_params.get('payment_status', '')
        company_id = _int_or_none(request.query_params.get('company_id'))
        search = (request.query_params.get('search') or '').strip()

        qs = B2BOrder.objects.filter(brand_id=brand_id)
        if status:
            qs = qs.filter(status=status)
        if payment_status:
            qs = qs.filter(payment_status=payment_status)
        if company_id:
            qs = qs.filter(company_id=company_id)
        if search:
            qs = qs.filter(
                Q(id__icontains=search) | Q(company__company_name__icontains=search),
            )

        total = qs.count()
        offset = (page - 1) * per_page
        rows = (
            qs.select_related('company')
            .prefetch_related('items', 'returns')
            .order_by('-order_date')[offset:offset + per_page]
        )
        # ok() nests data; add pagination alongside it so both this page and the
        # CRM company-detail page (which reads res.data as the array) work.
        response = ok([_order_row(o) for o in rows])
        response.data['total'] = total
        response.data['page'] = page
        response.data['per_page'] = per_page
        return response

    # ── POST dispatch ──
    def post(self, request):
        brand_id = current_brand_id(request)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        handlers = {
            'create_order': self._create_order,
            'update_order': self._update_order,
            'confirm_order': self._confirm_order,
            'update_status': self._update_status,
            'add_payment': self._add_payment,
            'return_items': self._return_items,
            'repeat_order': self._repeat_order,
            'delete_order': self._delete_order,
            'generate_credit_note': self._generate_credit_note,
            'gst_preview': self._gst_preview,
            'set_eway_bill': self._set_eway_bill,
            'cancel_reissue': self._cancel_reissue,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request, brand_id, body)

    # ── create / update ──
    def _parse_items(self, brand_id, raw_items):
        """Return (valid_items, subtotal, offer_discount) mirroring the PHP loop."""
        valid = []
        subtotal = Decimal('0')
        offer_discount = Decimal('0')
        for item in raw_items or []:
            fid = _int_or_none(item.get('flavor_id'))
            weight = _int_or_none(item.get('weight_grams'))
            fname = (item.get('flavor_name') or '').strip()
            if not fid or not weight or not fname:
                continue
            qty = _int_or_none(item.get('quantity')) or 1
            sp = _decimal_or_zero(item.get('selling_price'))
            is_free = 1 if _int_or_none(item.get('is_free_item')) else 0
            if is_free:
                offer_discount += sp * qty
                sp = Decimal('0')
            subtotal += sp * qty
            pack_id = _int_or_none(item.get('flavor_pack_id'))
            pack_label = (item.get('pack_label') or '').strip() or None
            mrp = _decimal_or_none(item.get('mrp'))
            if pack_id and not FlavorPack.objects.filter(id=pack_id, brand_id=brand_id).exists():
                pack_id = None
                pack_label = None
                mrp = None
            valid.append({
                'flavor_id': fid,
                'flavor_pack_id': pack_id,
                'stock_id': _int_or_none(item.get('stock_id')),
                'quantity': qty,
                'weight_grams': weight,
                'total_weight_grams': qty * weight,
                'mrp': mrp,
                'selling_price': sp,
                'cost_price': _decimal_or_none(item.get('cost_price')),
                'is_free_item': bool(is_free),
                'offer_id': _int_or_none(item.get('offer_id')),
                'flavor_name': fname,
                'pack_label': pack_label,
            })
        return valid, subtotal, offer_discount

    def _resolve_due_date(self, body, company):
        raw = body.get('due_date')
        if raw:
            return raw
        if company and company.payment_terms_days > 0:
            return date.today() + timedelta(days=company.payment_terms_days)
        return None

    def _compute_discount(self, disc_type, disc_val, subtotal):
        if disc_type == 'percentage' and disc_val and disc_val > 0:
            return (subtotal * disc_val / 100).quantize(Decimal('0.01'))
        if disc_type == 'amount' and disc_val and disc_val > 0:
            return min(disc_val, subtotal)
        return Decimal('0')

    @transaction.atomic
    def _create_order(self, request, brand_id, body):
        company_id = _int_or_none(body.get('company_id'))
        raw_items = body.get('items') or []
        if not company_id or not raw_items:
            return err('Company and at least one item required.')
        company = B2BCompany.objects.filter(id=company_id, brand_id=brand_id).first()
        if not company:
            return err('Company not found.')

        valid, subtotal, offer_discount = self._parse_items(brand_id, raw_items)
        if not valid:
            return err('No valid items.')

        payment_amount = _decimal_or_zero(body.get('payment_amount'))
        payment_method = body.get('payment_method') or ''
        if payment_amount > 0 and not payment_method:
            return err('Payment method is required to record a payment.')

        disc_type = body.get('discount_type') or ''
        disc_val = _decimal_or_none(body.get('discount_value'))
        disc_amt = self._compute_discount(disc_type, disc_val, subtotal)
        total = max(Decimal('0'), subtotal - disc_amt)

        ship, ship_err = _ship_to_fields(body)
        if ship_err:
            return err(ship_err)
        price_mode = billing.price_mode_setting('b2b') if billing.gst_active(brand_id) else ''
        gst_amount = Decimal('0')
        if price_mode:
            try:
                gst_amount, total, _result = billing.b2b_estimate(
                    brand_id, company, valid, disc_amt, price_mode,
                    ship['ship_to_same_as_bill_to'], ship['ship_to_state_code'],
                )
            except (billing.BillingError, ValueError) as exc:
                return err(str(exc))

        initial_status = body.get('status') or B2BOrder.Status.DRAFT
        if initial_status not in B2BOrder.Status.values:
            initial_status = B2BOrder.Status.DRAFT

        brand = Brand.objects.filter(id=brand_id).first()
        prefix = brand.order_prefix if brand else 'ORD'
        order_id = f'{prefix}B_' + uuid4().hex[:13]

        order = B2BOrder.objects.create(
            id=order_id, brand_id=brand_id, company=company,
            contact_id=_int_or_none(body.get('contact_id')),
            status=B2BOrder.Status.DRAFT,
            subtotal=subtotal, discount_type=disc_type,
            discount_value=disc_val or 0, discount_amount=disc_amt,
            offer_discount_amount=offer_discount,
            total_amount=total, balance_amount=total,
            due_date=self._resolve_due_date(body, company),
            source_order_id=body.get('source_order_id') or None,
            notes=(body.get('notes') or '').strip(),
            created_by=request.user,
            order_date=_parse_dt(body.get('order_date')) or timezone.now(),
            price_mode=price_mode, gst_amount=gst_amount,
            customer_po_no=(body.get('customer_po_no') or '').strip()[:100],
            customer_po_date=billing.parse_iso_date(body.get('customer_po_date')),
            **ship,
        )
        order.place_of_supply_state = billing.b2b_place_of_supply(order, company)
        order.save(update_fields=['place_of_supply_state'])
        self._save_items(order, valid)
        B2BActivity.objects.create(
            company=company, admin_user=request.user, order=order,
            type=B2BActivity.Type.ORDER,
            subject=f'Order {order_id} created',
            description=f'Total: {total:.2f}',
        )

        if company.stage != B2BCompany.Stage.CONVERTED:
            old_stage = company.stage
            company.stage = B2BCompany.Stage.CONVERTED
            company.converted_at = timezone.now()
            company.save(update_fields=['stage', 'converted_at', 'updated_at'])
            B2BActivity.objects.create(
                company=company, admin_user=request.user,
                type=B2BActivity.Type.STAGE_CHANGE,
                subject=f'Stage changed from {old_stage} to converted',
                description=f'Auto-converted by creating order {order_id}',
                old_stage=old_stage, new_stage=B2BCompany.Stage.CONVERTED,
            )

        if payment_amount > 0:
            self._add_payment(request, brand_id, {
                'company_id': company_id,
                'amount': body.get('payment_amount'),
                'payment_method': payment_method,
                'payment_type': 'payment',
                'reference_number': body.get('payment_reference') or '',
                'notes': body.get('payment_notes') or '',
                'payment_date': body.get('payment_date') or None,
                'order_id': order_id,
            })

        if initial_status != B2BOrder.Status.DRAFT:
            status_result = self._update_status(request, brand_id, {
                'order_id': order_id, 'status': initial_status,
            })
            if not status_result.data['success']:
                transaction.set_rollback(True)
                return status_result

        return ok({'order_id': order_id}, 'Order created!')

    @transaction.atomic
    def _update_order(self, request, brand_id, body):
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).select_related('company').first()
        company_id = _int_or_none(body.get('company_id'))
        raw_items = body.get('items') or []
        if not order or not company_id or not raw_items:
            return err('Order ID, company and at least one item required.')
        if order.status != B2BOrder.Status.DRAFT:
            return err('Only draft orders can be edited.')
        frozen = billing.freeze_error(order)
        if frozen:
            return err(frozen)
        company = B2BCompany.objects.filter(id=company_id, brand_id=brand_id).first()
        if not company:
            return err('Company not found.')

        valid, subtotal, offer_discount = self._parse_items(brand_id, raw_items)
        if not valid:
            return err('No valid items.')

        disc_type = body.get('discount_type') or ''
        disc_val = _decimal_or_none(body.get('discount_value'))
        disc_amt = self._compute_discount(disc_type, disc_val, subtotal)
        total = max(Decimal('0'), subtotal - disc_amt)

        ship, ship_err = _ship_to_fields(body)
        if ship_err:
            return err(ship_err)
        gst_amount = Decimal('0')
        if order.price_mode:
            try:
                gst_amount, total, _result = billing.b2b_estimate(
                    brand_id, company, valid, disc_amt, order.price_mode,
                    ship['ship_to_same_as_bill_to'], ship['ship_to_state_code'],
                )
            except (billing.BillingError, ValueError) as exc:
                return err(str(exc))
        for key, value in ship.items():
            setattr(order, key, value)
        order.gst_amount = gst_amount
        order.customer_po_no = (body.get('customer_po_no') or '').strip()[:100]
        order.customer_po_date = billing.parse_iso_date(body.get('customer_po_date'))
        order.place_of_supply_state = billing.b2b_place_of_supply(order, company)

        order.company = company
        order.contact_id = _int_or_none(body.get('contact_id'))
        order.subtotal = subtotal
        order.discount_type = disc_type
        order.discount_value = disc_val or 0
        order.discount_amount = disc_amt
        order.offer_discount_amount = offer_discount
        order.total_amount = total
        order.balance_amount = total - order.paid_amount
        order.due_date = self._resolve_due_date(body, company)
        order.notes = (body.get('notes') or '').strip()
        order.save()

        order.items.all().delete()
        self._save_items(order, valid)
        sync_b2b_order_income(order)
        return ok({'order_id': order.id}, 'Order updated!')

    def _save_items(self, order, valid):
        B2BOrderItem.objects.bulk_create([
            B2BOrderItem(
                b2b_order=order,
                flavor_id=vi['flavor_id'],
                flavor_pack_id=vi['flavor_pack_id'],
                stock_id=vi['stock_id'],
                quantity=vi['quantity'],
                weight_grams=vi['weight_grams'],
                total_weight_grams=vi['total_weight_grams'],
                mrp=vi['mrp'],
                selling_price=vi['selling_price'],
                cost_price=vi['cost_price'],
                is_free_item=vi['is_free_item'],
                offer_id=vi['offer_id'],
                flavor_name=vi['flavor_name'],
                pack_label=vi['pack_label'],
            )
            for vi in valid
        ])

    # ── confirm (deduct stock) ──
    @transaction.atomic
    def _confirm_order(self, request, brand_id, body):
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).first()
        if not order:
            return err('Order not found.')
        if order.status != B2BOrder.Status.DRAFT:
            return err('Only draft orders can be confirmed.')

        if not order.stock_deducted:
            error = self._deduct_order_stock(order, request.user)
            if error:
                return error
            order.stock_deducted = True

        order.status = B2BOrder.Status.CONFIRMED
        order.save(update_fields=['status', 'stock_deducted', 'updated_at'])
        return ok(None, 'Order confirmed and stock deducted.')

    def _deduct_order_stock(self, order, user):
        """
        Deduct stock for every line of `order`. Lines pinned to a batch
        (`item.stock_id`, chosen by the admin in the order-create form) are
        deducted from that exact batch — NOT the FIFO active batch, which is
        a B2C-only concept. Unpinned lines fall back to FIFO deduction.
        Returns an error response if stock is insufficient, else None.
        """
        items = list(order.items.select_related('flavor', 'stock'))
        batch_needed = {}
        flavor_needed = {}
        pinned_by_flavor = {}
        for it in items:
            if it.stock_id:
                batch_needed[it.stock_id] = batch_needed.get(it.stock_id, 0) + it.total_weight_grams
                pinned_by_flavor[it.flavor_id] = pinned_by_flavor.get(it.flavor_id, 0) + it.total_weight_grams
            else:
                flavor_needed[it.flavor_id] = flavor_needed.get(it.flavor_id, 0) + it.total_weight_grams

        seen_batches = {}
        seen_flavors = {}
        for it in items:
            if it.stock_id and it.stock_id not in seen_batches:
                seen_batches[it.stock_id] = it
                needed = batch_needed[it.stock_id]
                if not it.stock or it.stock.quantity_grams < needed:
                    avail = it.stock.quantity_grams if it.stock else 0
                    return err(
                        f'Insufficient stock in batch for {it.flavor_name}. '
                        f'Available: {avail}g, needed: {needed}g.',
                    )
            elif not it.stock_id and it.flavor_id not in seen_flavors:
                seen_flavors[it.flavor_id] = it
                needed = flavor_needed[it.flavor_id]
                # Grams already claimed by this order's pinned batches for the
                # same flavor are counted in available_grams() but are NOT
                # available to unpinned lines — subtract them out.
                avail = available_grams(it.flavor) - pinned_by_flavor.get(it.flavor_id, 0)
                if avail < needed:
                    return err(
                        f'Insufficient stock for {it.flavor_name}. '
                        f'Available: {avail}g, needed: {needed}g.',
                    )

        for it in items:
            if it.stock_id and it.stock:
                deduct_from_batch(
                    it.stock, it.total_weight_grams,
                    reference_type='b2b_sale', reference_id=order.id,
                    created_by=user, notes=f'B2B order {order.id}',
                )
            else:
                deduct_stock(
                    it.flavor, it.total_weight_grams,
                    reference_type='b2b_sale', reference_id=order.id,
                    created_by=user, notes=f'B2B order {order.id}',
                )
        return None

    # ── status update ──
    # Any status is reachable from any other. Stock is deducted iff the
    # target status is one of confirmed/dispatched/delivered — moving to
    # draft/cancelled always reverts it, moving away from draft/cancelled
    # always (re-)deducts it, subject to availability.
    @transaction.atomic
    def _update_status(self, request, brand_id, body):
        target = body.get('status') or ''
        if target not in ('draft', 'confirmed', 'dispatched', 'delivered', 'cancelled'):
            return err('Invalid status.')
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).first()
        if not order:
            return err('Order not found.')
        if target == order.status:
            return ok(None, 'No change.')
        blocked = billing.status_change_error(order, target)
        if blocked:
            return err(blocked)
        previous = order.status

        should_be_deducted = target in ('confirmed', 'dispatched', 'delivered')
        if should_be_deducted and not order.stock_deducted:
            error = self._deduct_order_stock(order, request.user)
            if error:
                return error
            order.stock_deducted = True
        elif not should_be_deducted and order.stock_deducted:
            self._revert_order_stock(order, request.user)

        order.status = target
        update_fields = ['status', 'stock_deducted', 'updated_at']
        if target == 'delivered' and order.delivered_at is None:
            order.delivered_at = timezone.now()
            update_fields.append('delivered_at')
        order.save(update_fields=update_fields)
        invoice = None
        if previous not in billing.B2B_INVOICE_STATUSES and billing.needs_invoice(order, target):
            try:
                invoice = billing.ensure_invoice(order, request.user)
            except billing.BillingError as exc:
                transaction.set_rollback(True)
                return err(str(exc))
            order.refresh_from_db()
        sync_b2b_order_income(order)
        message = f'Order status updated to {target}.'
        if invoice:
            message += f' GST invoice {invoice.number} issued.'
            if billing.eway_bill_required(invoice.is_interstate, invoice.grand_total):
                message += ' E-way bill required before the goods move.'
        return ok({'invoice': billing.invoice_payload(invoice)}, message)

    def _revert_order_stock(self, order, user):
        for it in order.items.select_related('flavor', 'stock'):
            if it.stock_id and it.stock:
                restock_batch(
                    it.stock, it.total_weight_grams,
                    reference_type='b2b_reversal', reference_id=order.id,
                    created_by=user, notes=f'Stock reverted — B2B order {order.id}',
                )
            else:
                revert_stock(
                    it.flavor, it.total_weight_grams,
                    reference_type='b2b_reversal', reference_id=order.id,
                    created_by=user, notes=f'Stock reverted — B2B order {order.id}',
                )
        order.stock_deducted = False

    # ── payment ──
    @transaction.atomic
    def _add_payment(self, request, brand_id, body):
        company_id = _int_or_none(body.get('company_id'))
        amount = _decimal_or_zero(body.get('amount'))
        method = body.get('payment_method') or ''
        if not company_id or amount <= 0 or not method:
            return err('Company, amount and payment method required.')
        company = B2BCompany.objects.filter(id=company_id, brand_id=brand_id).first()
        if not company:
            return err('Company not found.')

        pay_type = body.get('payment_type') or 'payment'
        order_id = body.get('order_id') or None
        order = None
        if order_id:
            order = B2BOrder.objects.filter(id=order_id, brand_id=brand_id).first()

        B2BPayment.objects.create(
            company=company, b2b_order=order, amount=amount,
            payment_type=pay_type, payment_method=method,
            reference_number=(body.get('reference_number') or '').strip(),
            notes=(body.get('notes') or '').strip(),
            payment_date=body.get('payment_date') or date.today(),
            created_by=request.user,
        )

        if order:
            sign = Decimal('-1') if pay_type == 'refund' else Decimal('1')
            order.paid_amount = order.paid_amount + sign * amount
            order.balance_amount = order.total_amount - order.paid_amount
            if order.paid_amount >= order.total_amount:
                order.payment_status = B2BOrder.PaymentStatus.PAID
            elif order.paid_amount > 0:
                order.payment_status = B2BOrder.PaymentStatus.PARTIAL
            else:
                order.payment_status = B2BOrder.PaymentStatus.PENDING
            order.save(update_fields=[
                'paid_amount', 'balance_amount', 'payment_status', 'updated_at',
            ])
            sync_b2b_order_income(order)

        B2BActivity.objects.create(
            company=company, admin_user=request.user, order=order,
            type=B2BActivity.Type.PAYMENT,
            subject=f'{pay_type.capitalize()} of {amount:.2f} via {method}',
            description=f'Against order {order_id}' if order_id else 'General payment',
        )
        return ok(None, 'Payment recorded!')

    # ── returns (partial or full; restocks the exact batch). Orders with a
    #    GST invoice need a credit note: the return records the goods and
    #    stock now, and the money side waits for `generate_credit_note`. ──
    @transaction.atomic
    def _return_items(self, request, brand_id, body):
        order = (
            B2BOrder.objects
            .select_for_update()
            .filter(id=body.get('order_id'), brand_id=brand_id)
            .select_related('company')
            .first()
        )
        if not order:
            return err('Order not found.')
        if not order.stock_deducted:
            return err('Only orders with deducted stock (confirmed/dispatched/delivered) can be returned.')

        raw_items = body.get('items') or []
        item_ids = [_int_or_none(ri.get('item_id')) for ri in raw_items]
        order_items = {
            it.id: it for it in
            B2BOrderItem.objects.select_related('flavor', 'stock').filter(
                id__in=[i for i in item_ids if i], b2b_order=order,
            )
        }

        lines = []
        return_amount = Decimal('0')
        for ri in raw_items:
            item = order_items.get(_int_or_none(ri.get('item_id')))
            qty = _int_or_none(ri.get('quantity')) or 0
            if not item or qty <= 0:
                continue
            remaining = item.quantity - item.returned_quantity
            if qty > remaining:
                return err(f'Cannot return {qty} of {item.flavor_name} — only {remaining} left to return.')
            weight = qty * item.weight_grams
            amount = (Decimal('0') if item.is_free_item else item.selling_price * qty)
            restock = ri.get('restock', True) not in (False, 0, '0', 'false', 'False')
            lines.append({'item': item, 'quantity': qty, 'weight': weight, 'amount': amount, 'restock': restock})
            return_amount += amount

        if not lines:
            return err('No valid items to return.')

        invoice = billing.active_invoice(order)
        preview = None
        if invoice:
            inv_lines = {ln.b2b_item_id: ln for ln in invoice.lines.filter(kind='GOODS')}
            selections = []
            for line in lines:
                inv_line = inv_lines.get(line['item'].id)
                if inv_line is None:
                    return err(f'{line["item"].flavor_name} is not on invoice {invoice.number}.')
                selections.append((inv_line, line['quantity'], line['restock']))
            try:
                preview = billing.preview_totals(billing.preview_credit_note(invoice, selections))
            except billing.BillingError as exc:
                return err(str(exc))
            return_amount = preview['grand_total']

        refund_method = body.get('refund_method') or 'cash'
        notes = (body.get('notes') or '').strip()

        for line in lines:
            item = line['item']
            if line['restock']:
                if item.stock_id and item.stock:
                    restock_batch(
                        item.stock, line['weight'],
                        reference_type='b2b_return', reference_id=order.id,
                        created_by=request.user, notes=f'Return — B2B order {order.id}',
                    )
                else:
                    revert_stock(
                        item.flavor, line['weight'],
                        reference_type='b2b_return', reference_id=order.id,
                        created_by=request.user, notes=f'Return — B2B order {order.id}',
                    )
            item.returned_quantity += line['quantity']
            item.save(update_fields=['returned_quantity'])

        refund_amount = Decimal('0')
        if not invoice:
            refund_amount = _apply_return_money(order, return_amount, refund_method, request.user)

        ret = B2BReturn.objects.create(
            b2b_order=order,
            items=[{
                'item_id': line['item'].id,
                'flavor_name': line['item'].flavor_name,
                'quantity': line['quantity'],
                'weight_grams': line['weight'],
                'amount': float(line['amount']),
                'restock': line['restock'],
            } for line in lines],
            return_amount=return_amount,
            refund_amount=refund_amount,
            notes=notes,
            needs_credit_note=invoice is not None,
            created_by=request.user,
        )
        summary = ', '.join(f"{line['item'].flavor_name} x{line['quantity']}" for line in lines)
        not_restocked = [line['item'].flavor_name for line in lines if not line['restock']]
        if invoice:
            outcome = 'Credit note pending.'
        elif refund_amount > 0:
            outcome = f'Refunded {refund_amount:.2f} via {refund_method}.'
        else:
            outcome = 'Bill reduced, no refund due.'
        B2BActivity.objects.create(
            company=order.company, admin_user=request.user, order=order,
            type=B2BActivity.Type.RETURN,
            subject=f'Return of {return_amount:.2f} on order {order.id}',
            description=(
                f'{summary}. {outcome}'
                + (f' Not restocked: {", ".join(not_restocked)}.' if not_restocked else '')
                + (f' {notes}' if notes else '')
            ),
        )
        if invoice:
            return ok({
                'return_id': ret.id,
                'credit_note_pending': True,
                'invoice_number': invoice.number,
                'preview': {k: float(v) for k, v in preview.items()},
            }, 'Return recorded. Generate the credit note to adjust the bill.')
        message = (
            f'Return processed. ₹{refund_amount:.2f} refunded.' if refund_amount > 0
            else f'Return processed. Bill reduced by ₹{return_amount:.2f}.'
        )
        return ok(None, message)

    # ── credit note for a pending return (applies the money side) ──
    @transaction.atomic
    def _generate_credit_note(self, request, brand_id, body):
        ret = (
            B2BReturn.objects.select_for_update()
            .filter(id=_int_or_none(body.get('return_id')), b2b_order__brand_id=brand_id)
            .first()
        )
        if not ret:
            return err('Return not found.')
        if not ret.credit_note_pending:
            return err('This return has no pending credit note.')
        order = B2BOrder.objects.select_for_update().select_related('company').get(pk=ret.b2b_order_id)
        invoice = billing.active_invoice(order)
        if invoice is None:
            return err('The order has no active GST invoice.')
        inv_lines = {ln.b2b_item_id: ln for ln in invoice.lines.filter(kind='GOODS')}
        selections = []
        for row in ret.items:
            inv_line = inv_lines.get(row.get('item_id'))
            if inv_line is None:
                return err(f'{row.get("flavor_name")} is not on invoice {invoice.number}.')
            selections.append((inv_line, int(row.get('quantity') or 0), bool(row.get('restock', True))))
        try:
            cn = billing.issue_credit_note(invoice, selections, 'sales_return', request.user, notes=ret.notes)
        except billing.BillingError as exc:
            transaction.set_rollback(True)
            return err(str(exc))

        refund_method = body.get('refund_method') or 'cash'
        refund = _apply_return_money(order, cn.grand_total, refund_method, request.user)
        ret.credit_note = cn
        ret.return_amount = cn.grand_total
        ret.refund_amount = refund
        ret.save(update_fields=['credit_note', 'return_amount', 'refund_amount'])
        B2BActivity.objects.create(
            company=order.company, admin_user=request.user, order=order,
            type=B2BActivity.Type.RETURN,
            subject=f'Credit note {cn.number} for ₹{cn.grand_total:.2f} on order {order.id}',
            description=(f'Refunded {refund:.2f} via {refund_method}.' if refund > 0 else 'Bill reduced, no refund due.'),
        )
        message = f'Credit note {cn.number} generated.'
        message += f' ₹{refund:.2f} refunded.' if refund > 0 else f' Bill reduced by ₹{cn.grand_total:.2f}.'
        return ok({'credit_note': billing.credit_note_payload(cn)}, message)

    # ── GST breakup for the order-create screen ──
    def _gst_preview(self, request, brand_id, body):
        company = B2BCompany.objects.filter(id=_int_or_none(body.get('company_id')), brand_id=brand_id).first()
        valid, subtotal, _offer = self._parse_items(brand_id, body.get('items') or [])
        if not company or not valid:
            return ok({'enabled': False})
        existing = B2BOrder.objects.filter(id=body.get('order_id') or '', brand_id=brand_id).first()
        if existing:
            price_mode = existing.price_mode
        else:
            price_mode = billing.price_mode_setting('b2b') if billing.gst_active(brand_id) else ''
        if not price_mode:
            return ok({'enabled': False})
        disc_amt = self._compute_discount(
            body.get('discount_type') or '', _decimal_or_none(body.get('discount_value')), subtotal,
        )
        ship, ship_err = _ship_to_fields(body, require_address=False)
        try:
            _gst, _total, result = billing.b2b_estimate(
                brand_id, company, valid, disc_amt, price_mode,
                ship['ship_to_same_as_bill_to'], ship['ship_to_state_code'],
            )
        except (billing.BillingError, ValueError) as exc:
            return ok({'enabled': True, 'error': str(exc), 'price_mode': price_mode})
        pos = billing.b2b_place_of_supply(SimpleNamespace(**ship), company)
        payload = billing.tax_result_payload(result)
        payload.update({
            'enabled': True, 'place_of_supply': state_label(pos),
            'eway_required': billing.eway_bill_required(result.is_interstate, result.grand_total),
        })
        return ok(payload)

    def _set_eway_bill(self, request, brand_id, body):
        order = B2BOrder.objects.filter(id=body.get('order_id'), brand_id=brand_id).first()
        invoice = billing.active_invoice(order) if order else None
        if not invoice:
            return err('The order has no active GST invoice.')
        billing.set_eway_bill(invoice, body.get('eway_bill_no'), billing.parse_iso_date(body.get('eway_bill_date')))
        return ok(None, 'E-way bill saved.')

    # ── cancel & reissue (wrong GSTIN / state / address on an issued invoice) ──
    def _cancel_reissue(self, request, brand_id, body):
        order = B2BOrder.objects.filter(id=body.get('order_id'), brand_id=brand_id).select_related('company').first()
        if not order:
            return err('Order not found.')
        if any(r.credit_note_pending for r in order.returns.all()):
            return err('Generate the pending credit note(s) on this order first.')
        gstin, gstin_err = validate_gstin(body.get('gst_number'))
        if gstin_err:
            return err(gstin_err)
        state_code = gstin[:2] if gstin else normalise_state_code(body.get('state_code'))
        if not state_code:
            return err('Enter a GSTIN or select the state of the invoice address.')
        ship, ship_err = _ship_to_fields(body)
        if ship_err:
            return err(ship_err)

        def apply_corrections(locked):
            company = locked.company
            company.gst_number = gstin
            company.gst_legal_name = (body.get('gst_legal_name') or '').strip()[:255]
            company.address = (body.get('address') if body.get('address') is not None else company.address).strip()
            company.city = (body.get('city') if body.get('city') is not None else company.city).strip()
            company.pincode = (body.get('pincode') if body.get('pincode') is not None else company.pincode).strip()
            company.state_code = state_code
            company.state = billing.state_name(state_code)
            company.save()
            for key, value in ship.items():
                setattr(locked, key, value)
            locked.place_of_supply_state = billing.b2b_place_of_supply(locked, company)
            locked.save()

        try:
            cn, new_invoice = billing.cancel_and_reissue(order, request.user, apply_corrections)
        except billing.BillingError as exc:
            return err(str(exc))
        B2BActivity.objects.create(
            company=order.company, admin_user=request.user, order=order, type=B2BActivity.Type.NOTE,
            subject=f'Invoice reissued as {new_invoice.number}',
            description=f'Credit note {cn.number} cancels the earlier invoice.',
        )
        return ok({'invoice': billing.invoice_payload(new_invoice)},
                  f'Credit note {cn.number} issued and new invoice {new_invoice.number} created.')

    # ── repeat / edit source ──
    def _repeat_order(self, request, brand_id, body):
        source_id = body.get('source_order_id') or ''
        order = (
            B2BOrder.objects
            .filter(id=source_id, brand_id=brand_id)
            .first()
        )
        if not order:
            return err('Source order not found.')
        items = list(order.items.select_related('stock').order_by('id'))
        if not items:
            return err('Source order not found.')
        return ok({
            'company_id': order.company_id,
            'contact_id': order.contact_id,
            'discount_type': order.discount_type,
            'discount_value': float(order.discount_value),
            'notes': order.notes,
            'due_date': order.due_date.isoformat() if order.due_date else None,
            'order_date': order.order_date.isoformat(),
            'items': [_item_row(it) for it in items],
            'source_order_id': source_id,
            'ship_to_same_as_bill_to': 1 if order.ship_to_same_as_bill_to else 0,
            'ship_to_name': order.ship_to_name,
            'ship_to_address': order.ship_to_address,
            'ship_to_state_code': order.ship_to_state_code,
            'ship_to_pincode': order.ship_to_pincode,
            'ship_to_gstin': order.ship_to_gstin,
            'customer_po_no': order.customer_po_no,
            'customer_po_date': order.customer_po_date.isoformat() if order.customer_po_date else None,
        })

    # ── delete ──
    @transaction.atomic
    def _delete_order(self, request, brand_id, body):
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).first()
        if not order:
            return err('Order not found.')
        if order.invoices.exists():
            return err(billing.freeze_error(order, 'delet') or 'This order has GST invoices and cannot be deleted.')
        if order.stock_deducted:
            self._revert_order_stock(order, request.user)
        order.payments.all().delete()
        order.items.all().delete()
        order.delete()
        return ok(None, 'Order deleted and stock reverted.')


# ── B2B offers API ─────────────────────────────────────────────────────────────

class B2BOffersAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    def get(self, request):
        brand_id = current_brand_id(request)
        offers = (
            B2BOffer.objects
            .filter(brand_id=brand_id)
            .select_related('company')
            .order_by('-is_active', '-created_at')
        )
        return ok([_offer_row(o) for o in offers])

    def post(self, request):
        brand_id = current_brand_id(request)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        if action == 'add':
            return self._save(request, brand_id, body, None)
        if action == 'edit':
            offer = B2BOffer.objects.filter(
                id=_int_or_none(body.get('id')), brand_id=brand_id,
            ).first()
            if not offer:
                return err('Error updating offer.')
            return self._save(request, brand_id, body, offer)
        if action == 'toggle':
            offer = B2BOffer.objects.filter(
                id=_int_or_none(body.get('id')), brand_id=brand_id,
            ).first()
            if not offer:
                return err('Error updating.')
            offer.is_active = not offer.is_active
            offer.save(update_fields=['is_active', 'updated_at'])
            return ok(None, 'Offer status updated!')
        if action == 'delete':
            offer = B2BOffer.objects.filter(
                id=_int_or_none(body.get('id')), brand_id=brand_id,
            ).first()
            if not offer:
                return err('Error deleting.')
            offer.delete()
            return ok(None, 'Offer deleted!')
        return err('Unknown action.')

    def _save(self, request, brand_id, body, offer):
        name = (body.get('name') or '').strip()
        offer_type = body.get('type') or ''
        if not name or offer_type not in OFFER_TYPES:
            return err('Name and valid type required.')
        company_id = _int_or_none(body.get('company_id'))
        if company_id and not B2BCompany.objects.filter(
            id=company_id, brand_id=brand_id,
        ).exists():
            return err('Invalid company.')
        fields = {
            'company_id': company_id,
            'name': name,
            'description': (body.get('description') or '').strip(),
            'type': offer_type,
            'min_quantity': _int_or_none(body.get('min_quantity')),
            'free_quantity': _int_or_none(body.get('free_quantity')),
            'discount_value': _decimal_or_none(body.get('discount_value')),
            'is_stackable': bool(_int_or_none(body.get('is_stackable'))),
            'valid_from': body.get('valid_from') or None,
            'valid_to': body.get('valid_to') or None,
        }
        if offer is None:
            offer = B2BOffer.objects.create(brand_id=brand_id, **fields)
            return ok({'id': offer.id}, 'Offer created!')
        for key, value in fields.items():
            setattr(offer, key, value)
        if body.get('is_active') is not None:
            offer.is_active = bool(_int_or_none(body.get('is_active')))
        offer.save()
        return ok(None, 'Offer updated!')


# ── helpers ──────────────────────────────────────────────────────────────────

def _apply_return_money(order, amount, refund_method, user):
    """Reduce the order total by `amount` and refund anything paid above the
    new total. Returns the refund amount."""
    new_total = max(Decimal('0'), order.total_amount - amount)
    order.total_amount = new_total
    refund_amount = Decimal('0')
    if order.paid_amount > new_total:
        refund_amount = order.paid_amount - new_total
        order.paid_amount -= refund_amount
        B2BPayment.objects.create(
            company=order.company, b2b_order=order, amount=refund_amount,
            payment_type=B2BPayment.PaymentType.REFUND, payment_method=refund_method,
            notes=f'Refund for return on order {order.id}',
            payment_date=date.today(), created_by=user,
        )
    order.balance_amount = order.total_amount - order.paid_amount
    if order.paid_amount >= order.total_amount:
        order.payment_status = B2BOrder.PaymentStatus.PAID
    elif order.paid_amount > 0:
        order.payment_status = B2BOrder.PaymentStatus.PARTIAL
    else:
        order.payment_status = B2BOrder.PaymentStatus.PENDING
    order.save(update_fields=[
        'total_amount', 'paid_amount', 'balance_amount', 'payment_status', 'updated_at',
    ])
    sync_b2b_order_income(order)
    return refund_amount


def _truthy(value, default=True):
    if value is None or value == '':
        return default
    return value not in (False, 0, '0', 'false', 'False', 'off')


def _ship_to_fields(body, require_address=True):
    """Delivery address from the order form. 'Same as invoice address' is
    the default."""
    same = _truthy(body.get('ship_to_same_as_bill_to'), True)
    if same:
        return {
            'ship_to_same_as_bill_to': True, 'ship_to_name': '', 'ship_to_address': '',
            'ship_to_state_code': '', 'ship_to_pincode': '', 'ship_to_gstin': '',
        }, None
    gstin, gstin_err = validate_gstin(body.get('ship_to_gstin'))
    fields = {
        'ship_to_same_as_bill_to': False,
        'ship_to_name': (body.get('ship_to_name') or '').strip()[:255],
        'ship_to_address': (body.get('ship_to_address') or '').strip(),
        'ship_to_state_code': normalise_state_code(body.get('ship_to_state_code')),
        'ship_to_pincode': (body.get('ship_to_pincode') or '').strip()[:10],
        'ship_to_gstin': gstin if not gstin_err else '',
    }
    if not require_address:
        return fields, None
    if gstin_err:
        return fields, f'Delivery address GSTIN: {gstin_err}'
    if not fields['ship_to_address'] or not fields['ship_to_state_code']:
        return fields, 'Enter the delivery address and state, or tick "Delivery address same as invoice address".'
    return fields, None


def _billing_payload(order):
    """Invoice, credit notes and GST data for the order view modal."""
    invoice = billing.active_invoice(order)
    invoices = list(order.invoices.order_by('id'))
    credit_notes = [cn for inv in invoices for cn in inv.credit_notes.order_by('id')]
    summary = billing.order_gst_summary(order)
    estimate = None
    if summary and not summary.get('unavailable'):
        estimate = {
            'tax_total': float(summary['tax_total']), 'cgst': float(summary['cgst']),
            'sgst': float(summary['sgst']), 'igst': float(summary['igst']),
            'is_interstate': summary['is_interstate'], 'place_of_supply': summary['place_of_supply'],
            'eway_required': billing.eway_bill_required(summary['is_interstate'], summary['grand_total']),
        }
    return {
        'gst_enabled': bool(order.price_mode) or invoice is not None,
        'invoice': billing.invoice_payload(invoice),
        'invoice_lines': billing.invoice_lines_payload(invoice) if invoice else [],
        'cancelled_invoices': [
            {'id': i.id, 'number': i.number, 'date': i.invoice_date.isoformat()}
            for i in invoices if i.status == Invoice.Status.CANCELLED
        ],
        'credit_notes': [billing.credit_note_payload(cn) for cn in credit_notes],
        'gst': estimate,
        'pending_returns': [r.id for r in order.returns.all() if r.credit_note_pending],
    }


def _int_or_none(value):
    try:
        return int(value) if value not in (None, '', 'null') else None
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value):
    if value in (None, '', 'null'):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _parse_dt(value):
    """Parse a `datetime-local` string into an aware datetime (or None)."""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _decimal_or_zero(value):
    return _decimal_or_none(value) or Decimal('0')
