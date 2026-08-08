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
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from core.models import Brand, Setting
from crm.models import B2BActivity, B2BCompany, B2BContact
from orders.models import B2BOffer, B2BOrder, B2BOrderItem, B2BPayment
from products.models import Flavor, FlavorPack, Stock
from products.services import available_grams, deduct_stock, revert_stock

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
        'due_date': o.due_date.isoformat() if o.due_date else None,
        'order_date': o.order_date.isoformat(),
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
    }


def _item_row(it):
    return {
        'id': it.id,
        'flavor_id': it.flavor_id,
        'flavor_pack_id': it.flavor_pack_id,
        'stock_id': it.stock_id,
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
            .filter(brand_id=brand_id, stage='converted', is_active=True)
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
        items = order.items.order_by('id')
        payments = order.payments.select_related('created_by').order_by('-payment_date')
        return ok({
            'order': _order_detail(order),
            'items': [_item_row(it) for it in items],
            'payments': [_payment_row(p) for p in payments],
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
            .prefetch_related('items')
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
            'repeat_order': self._repeat_order,
            'delete_order': self._delete_order,
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

        disc_type = body.get('discount_type') or ''
        disc_val = _decimal_or_none(body.get('discount_value'))
        disc_amt = self._compute_discount(disc_type, disc_val, subtotal)
        total = max(Decimal('0'), subtotal - disc_amt)

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
        )
        self._save_items(order, valid)
        B2BActivity.objects.create(
            company=company, admin_user=request.user,
            type=B2BActivity.Type.ORDER,
            subject=f'Order {order_id} created',
            description=f'Total: {total:.2f}',
        )
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
            items = list(order.items.select_related('flavor'))
            needed = {}
            for it in items:
                needed[it.flavor_id] = needed.get(it.flavor_id, 0) + it.total_weight_grams
            for it in items:
                if it.flavor_id in needed:
                    avail = available_grams(it.flavor)
                    if avail < needed[it.flavor_id]:
                        return err(
                            f'Insufficient stock for {it.flavor_name}. '
                            f'Available: {avail}g, needed: {needed[it.flavor_id]}g.',
                        )
                    del needed[it.flavor_id]
            for it in items:
                deduct_stock(
                    it.flavor, it.total_weight_grams,
                    reference_type='b2b_sale', reference_id=order.id,
                    created_by=request.user, notes=f'B2B order {order.id}',
                )
            order.stock_deducted = True

        order.status = B2BOrder.Status.CONFIRMED
        order.save(update_fields=['status', 'stock_deducted', 'updated_at'])
        return ok(None, 'Order confirmed and stock deducted.')

    # ── status update ──
    @transaction.atomic
    def _update_status(self, request, brand_id, body):
        status = body.get('status') or ''
        if status not in ('dispatched', 'delivered', 'cancelled'):
            return err('Invalid status.')
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).first()
        if not order:
            return err('Order not found.')
        if status == 'cancelled' and order.stock_deducted:
            self._revert_order_stock(order, request.user)
        order.status = status
        order.save(update_fields=['status', 'stock_deducted', 'updated_at'])
        return ok(None, f'Order status updated to {status}.')

    def _revert_order_stock(self, order, user):
        for it in order.items.select_related('flavor'):
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

        B2BActivity.objects.create(
            company=company, admin_user=request.user,
            type=B2BActivity.Type.PAYMENT,
            subject=f'{pay_type.capitalize()} of {amount:.2f} via {method}',
            description=f'Against order {order_id}' if order_id else 'General payment',
        )
        return ok(None, 'Payment recorded!')

    # ── repeat / edit source ──
    def _repeat_order(self, request, brand_id, body):
        source_id = body.get('source_order_id') or ''
        order = (
            B2BOrder.objects
            .filter(id=source_id, brand_id=brand_id)
            .prefetch_related('items')
            .first()
        )
        if not order:
            return err('Source order not found.')
        items = list(order.items.order_by('id'))
        if not items:
            return err('Source order not found.')
        return ok({
            'company_id': order.company_id,
            'contact_id': order.contact_id,
            'discount_type': order.discount_type,
            'discount_value': float(order.discount_value),
            'notes': order.notes,
            'items': [_item_row(it) for it in items],
            'source_order_id': source_id,
        })

    # ── delete ──
    @transaction.atomic
    def _delete_order(self, request, brand_id, body):
        order = B2BOrder.objects.filter(
            id=body.get('order_id'), brand_id=brand_id,
        ).first()
        if not order:
            return err('Order not found.')
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


def _decimal_or_zero(value):
    return _decimal_or_none(value) or Decimal('0')
