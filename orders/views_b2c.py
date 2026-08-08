"""
B2C orders + coupons admin slice (Phase 7).

Ported from the PHP `admin/orders.php`, `view-order.php`, `edit-order.php`,
`print-invoice.php`, `coupons.php` and their `admin/api/*` endpoints. Each API
mirrors its PHP counterpart: one endpoint serves GET (list / form data) and
POST (dispatched on an `action` field). Every query is scoped to the active
brand via `current_brand_id`. Stock deduction/reversal goes through the shared
`products.services` helpers — no stock math is re-implemented here.
"""
import json
from uuid import uuid4

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from accounts.models import PointsTransaction, User
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from core.models import Brand, Setting
from orders.models import Coupon, Order, OrderItem
from products.models import Flavor, FlavorPack
from products.services import available_grams, deduct_stock, revert_stock

ORDER_STATUSES = [
    'pending', 'confirmed', 'processing', 'shipped', 'delivered',
    'cancelled', 'failed', 'deleted',
]
PAYMENT_STATUSES = ['pending', 'paid', 'failed', 'refunded']
DEDUCT_STATUSES = ('shipped', 'delivered')
STOCK_CHECK_STATUSES = ('confirmed', 'processing', 'shipped', 'delivered')


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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


# ── Stock helpers (mirror the PHP checkStockForOrder / deductStockForOrder) ──

def _items_by_flavor(order):
    return (
        OrderItem.objects.filter(order_id=order.id, flavor__isnull=False)
        .values('flavor_id', 'flavor_name')
        .annotate(qty=Sum('quantity'))
    )


def _check_stock_for_order(order):
    """Return an error string if any flavor lacks available stock, else None."""
    errors = []
    for row in _items_by_flavor(order):
        flavor = Flavor.objects.filter(id=row['flavor_id']).first()
        if not flavor:
            continue
        needed = row['qty'] or 0
        avail = available_grams(flavor)
        if avail < needed:
            errors.append(
                f"{row['flavor_name']}: need {round(needed / 1000, 3)}kg, "
                f"only {round(avail / 1000, 3)}kg available"
            )
    return '; '.join(errors) if errors else None


def _deduct_stock_for_order(order, user):
    """Idempotent FIFO deduction for every item; marks the order deducted."""
    if order.stock_deducted:
        return
    for row in _items_by_flavor(order):
        flavor = Flavor.objects.filter(id=row['flavor_id']).first()
        if not flavor:
            continue
        deduct_stock(
            flavor, row['qty'] or 0,
            reference_type='sale', reference_id=order.id, created_by=user,
        )
    order.stock_deducted = True
    order.save(update_fields=['stock_deducted', 'updated_at'])


def _revert_stock_for_order(order, user):
    """Add stock back for a previously-deducted order that is now cancelled."""
    if not order.stock_deducted:
        return
    for row in _items_by_flavor(order):
        flavor = Flavor.objects.filter(id=row['flavor_id']).first()
        if not flavor:
            continue
        revert_stock(
            flavor, row['qty'] or 0,
            reference_type='sale', reference_id=order.id, created_by=user,
        )
    order.stock_deducted = False
    order.save(update_fields=['stock_deducted', 'updated_at'])


def _sync_loyalty(order_id, payment_status):
    """Credit/uncredit the storefront loyalty transaction for this order."""
    qs = PointsTransaction.objects.filter(reference=str(order_id), type='purchase')
    if payment_status == 'paid':
        qs.update(status='credited')
    else:
        qs.filter(status='credited').update(status='pending')


def _validate_coupon(coupon, sale_total, item_count, flavor_ids):
    """Port of config.php `validate_coupon`. Returns (discount, error_message)."""
    now = timezone.now()
    if coupon.valid_flavors:
        valid = {str(x) for x in coupon.valid_flavors}
        for fid in flavor_ids:
            if str(fid) not in valid:
                return None, 'Order contains flavors not qualified for this offer.'
    if coupon.start_date and now <= coupon.start_date:
        return None, 'This coupon is not yet active'
    if coupon.expiry_date and now > coupon.expiry_date:
        return None, 'This coupon has expired'
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        return None, 'This coupon has reached maximum usage limit'
    if coupon.min_order_value is not None and sale_total < coupon.min_order_value:
        return None, f'Minimum order value of ₹{coupon.min_order_value} required for this coupon'
    if coupon.min_quantity is not None and item_count < coupon.min_quantity:
        return None, f'Minimum order of quantity {coupon.min_quantity} required for this coupon'
    discount = 0.0
    if coupon.discount_amount:
        discount = coupon.discount_amount
    elif coupon.discount_percentage:
        discount = sale_total * coupon.discount_percentage / 100
        if coupon.max_discount_amount and discount > float(coupon.max_discount_amount):
            discount = float(coupon.max_discount_amount)
    return round(discount, 2), None


def _pack_prices(pack):
    """Convert a pack's absolute prices to per-kg (grams basis)."""
    sale = float(pack.selling_price) / pack.weight_grams * 1000
    mrp = float(pack.mrp) if pack.mrp else 0.0
    regular = mrp / pack.weight_grams * 1000 if mrp > 0 else sale
    return regular, sale


# ═══════════════════════════════════════════════════════ Orders pages ══

@admin_login_required
@require_module('orders')
@ensure_csrf_cookie
def orders_page(request):
    return render(request, 'admin/orders.html')


_TIMELINE_DEF = [
    ('Order Placed', {'pending', 'confirmed', 'processing', 'shipped', 'delivered'}),
    ('Confirmed', {'confirmed', 'processing', 'shipped', 'delivered'}),
    ('Processing', {'processing', 'shipped', 'delivered'}),
    ('Shipped', {'shipped', 'delivered'}),
    ('Delivered', {'delivered'}),
]
_STATUS_COLORS = {
    'pending': ('#f59e0b', '#fffbeb'), 'confirmed': ('#3b82f6', '#eff6ff'),
    'processing': ('#8b5cf6', '#f5f3ff'), 'shipped': ('#06b6d4', '#ecfeff'),
    'delivered': ('#16a34a', '#f0fdf4'), 'cancelled': ('#dc2626', '#fef2f2'),
    'failed': ('#dc2626', '#fef2f2'), 'deleted': ('#6b7280', '#f3f4f6'),
}
_PAYMENT_COLORS = {
    'pending': ('#f59e0b', '#fffbeb'), 'paid': ('#16a34a', '#f0fdf4'),
    'failed': ('#dc2626', '#fef2f2'), 'refunded': ('#6b7280', '#f3f4f6'),
}


def _order_or_redirect(request, pk):
    brand_id = current_brand_id(request)
    return Order.objects.filter(id=pk, brand_id=brand_id).select_related('user', 'referral').first()


def _view_items(order):
    items = []
    subtotal = 0.0
    for it in order.items.select_related('flavor').all():
        total = it.quantity * it.sale_price_per_kg / 1000
        subtotal += total
        items.append({
            'name': it.flavor_name,
            'pack_label': it.pack_label or '',
            'description': it.flavor.description if it.flavor else '',
            'quantity': it.quantity,
            'quantity_kg': it.quantity / 1000,
            'price_per_kg': it.price_per_kg,
            'sale_price_per_kg': it.sale_price_per_kg,
            'item_total': total,
        })
    return items, subtotal


def _loyalty_balance(user_id):
    if not user_id:
        return 0
    agg = PointsTransaction.objects.filter(
        user_id=user_id, status='credited',
    ).aggregate(bal=Coalesce(Sum('points'), 0))
    return agg['bal'] or 0


@admin_login_required
@require_module('orders')
@ensure_csrf_cookie
def view_order(request, pk):
    order = _order_or_redirect(request, pk)
    if not order:
        return redirect('orders:orders')

    items, subtotal = _view_items(order)
    discount = order.coupon_discount or 0
    total = subtotal - discount + (order.delivery_charge or 0)

    is_cancelled = order.status in ('cancelled', 'failed', 'deleted')
    timeline = [
        {'label': label, 'done': order.status in statuses, 'is_first': i == 0}
        for i, (label, statuses) in enumerate(_TIMELINE_DEF)
    ]
    sc, sbg = _STATUS_COLORS.get(order.status, ('#6b7280', '#f3f4f6'))
    pc, pbg = _PAYMENT_COLORS.get(order.payment_status, ('#6b7280', '#f3f4f6'))

    return render(request, 'admin/view-order.html', {
        'order': order,
        'items': items,
        'subtotal': subtotal,
        'discount': discount,
        'total': total,
        'loyalty_points': _loyalty_balance(order.user_id),
        'is_cancelled': is_cancelled,
        'timeline': timeline,
        'sc': sc, 'sbg': sbg, 'pc': pc, 'pbg': pbg,
    })


@admin_login_required
@require_module('orders')
@ensure_csrf_cookie
def edit_order(request, pk):
    order = _order_or_redirect(request, pk)
    if not order:
        return redirect('orders:orders')

    brand_id = current_brand_id(request)
    items = []
    subtotal = 0.0
    for it in order.items.all():
        total = it.quantity * it.sale_price_per_kg / 1000
        subtotal += total
        items.append({
            'item_id': it.id,
            'quantity': it.quantity,
            'flavor_id': it.flavor_id,
            'name': it.flavor_name,
            'price_per_kg': it.price_per_kg,
            'sale_price_per_kg': it.sale_price_per_kg,
            'pack_label': it.pack_label or '',
            'item_total': total,
        })
    discount = order.coupon_discount or 0
    final_total = subtotal + (order.delivery_charge or 0) - discount

    flavors = Flavor.objects.filter(brand_id=brand_id, is_active=True).order_by('name')
    packs = list(
        FlavorPack.objects.filter(flavor__brand_id=brand_id, is_active=True)
        .order_by('flavor_id', 'weight_grams')
    )
    packs_json = json.dumps([
        {
            'id': p.id, 'flavor_id': p.flavor_id, 'weight_grams': p.weight_grams,
            'label': p.label, 'selling_price': float(p.selling_price),
        }
        for p in packs
    ])
    coupons = Coupon.objects.filter(brand_id=brand_id, is_active=True).filter(
        Q(expiry_date__isnull=True) | Q(expiry_date__gte=timezone.now()),
    ).order_by('code')
    users = User.objects.filter(brand_id=brand_id).order_by('name')

    order_loyalty = PointsTransaction.objects.filter(
        reference=str(order.id), type='purchase',
    ).values('points', 'status').first()

    return render(request, 'admin/edit-order.html', {
        'order': order,
        'items': items,
        'subtotal': subtotal,
        'discount': discount,
        'final_total': final_total,
        'flavors': flavors,
        'packs_json': packs_json,
        'coupons': coupons,
        'users': users,
        'order_loyalty': order_loyalty,
    })


@admin_login_required
@require_module('orders')
def print_invoice(request, pk):
    order = _order_or_redirect(request, pk)
    if not order:
        return redirect('orders:orders')

    items, subtotal = _view_items(order)
    total_quantity = sum(it['quantity'] for it in items)
    discount = order.coupon_discount or 0
    total = subtotal - discount + (order.delivery_charge or 0)

    settings_map = {s.setting_key: s.setting_value for s in Setting.objects.all()}
    brand = Brand.objects.filter(id=current_brand_id(request)).first()

    return render(request, 'admin/print-invoice.html', {
        'order': order,
        'items': items,
        'subtotal': subtotal,
        'discount': discount,
        'total': total,
        'total_quantity': total_quantity,
        'loyalty_points': _loyalty_balance(order.user_id),
        'settings_map': settings_map,
        'brand': brand,
    })


# ══════════════════════════════════════════════════════════ Orders API ══

class OrdersAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'orders'

    def get(self, request):
        if request.query_params.get('view') == 'order_form_data':
            return self._order_form_data(request)
        return self._list(request)

    def _order_form_data(self, request):
        brand_id = current_brand_id(request)
        flavors = Flavor.objects.filter(brand_id=brand_id, is_active=True).order_by('name')
        packs = (
            FlavorPack.objects.filter(flavor__brand_id=brand_id, is_active=True)
            .order_by('flavor_id', 'weight_grams')
        )
        return ok({
            'flavors': [
                {
                    'id': f.id, 'name': f.name,
                    'price_per_kg': f.price_per_kg,
                    'sale_price_per_kg': f.sale_price_per_kg,
                }
                for f in flavors
            ],
            'packs': [
                {
                    'id': p.id, 'flavor_id': p.flavor_id,
                    'weight_grams': p.weight_grams, 'label': p.label,
                    'mrp': float(p.mrp), 'selling_price': float(p.selling_price),
                    'cost_price': float(p.cost_price),
                }
                for p in packs
            ],
        })

    def _list(self, request):
        brand_id = current_brand_id(request)
        params = request.query_params

        qs = Order.objects.filter(brand_id=brand_id).exclude(status='deleted')
        status = params.get('status')
        if status and status != 'all':
            qs = qs.filter(status=status)
        search = (params.get('search') or '').strip()
        if search:
            qs = qs.filter(
                Q(id__icontains=search) | Q(name__icontains=search)
                | Q(mobile__icontains=search) | Q(user__name__icontains=search)
            )
        if params.get('date_from'):
            qs = qs.filter(order_date__date__gte=params.get('date_from'))
        if params.get('date_to'):
            qs = qs.filter(order_date__date__lte=params.get('date_to'))
        qs = qs.distinct()

        total = qs.count()
        page = max(1, _int(params.get('page'), 1))
        per_page = _int(params.get('per_page'), 20)
        if per_page not in (10, 25, 50, 100):
            per_page = 20
        offset = (page - 1) * per_page

        orders = list(
            qs.select_related('user').prefetch_related('items')
            .order_by('-order_date')[offset:offset + per_page]
        )
        user_ids = [o.user_id for o in orders if o.user_id]
        loyalty = {}
        if user_ids:
            for row in (
                PointsTransaction.objects.filter(user_id__in=user_ids, status='credited')
                .values('user_id').annotate(pts=Sum('points'))
            ):
                loyalty[row['user_id']] = row['pts'] or 0

        rows = [self._order_row(o, loyalty) for o in orders]
        return ok({'orders': rows, 'total': total, 'page': page, 'per_page': per_page})

    @staticmethod
    def _order_row(o, loyalty):
        items = list(o.items.all())
        subtotal = sum(it.quantity * it.sale_price_per_kg / 1000 for it in items)
        discount = o.coupon_discount or 0
        delivery = o.delivery_charge or 0
        return {
            'id': o.id,
            'user_id': o.user_id,
            'status': o.status,
            'payment_status': o.payment_status,
            'name': o.name,
            'mobile': o.mobile,
            'address': o.address or '',
            'pincode': o.pincode or '',
            'delivery_charge': delivery,
            'order_date': o.order_date.isoformat(),
            'coupon_code': o.coupon_code or '',
            'coupon_discount': discount,
            'user_name': o.user.name if o.user else '',
            'current_loyalty_points': loyalty.get(o.user_id, 0),
            'subtotal': subtotal,
            'discount_amount': discount,
            'total_price': subtotal + delivery - discount,
            'items': ', '.join(f'{it.flavor_name} ({it.quantity}g)' for it in items),
            'item_count': len(items),
        }

    def post(self, request):
        handlers = {
            'update_status': self._update_status,
            'update_payment_status': self._update_payment_status,
            'update': self._update,
            'add_item': self._add_item,
            'remove_item': self._remove_item,
            'bulk_update': self._bulk_update,
        }
        handler = handlers.get(request.data.get('action', ''))
        if not handler:
            return err('Unknown action.')
        return handler(request)

    def _get_order(self, request, order_id):
        return Order.objects.filter(
            id=order_id or '', brand_id=current_brand_id(request),
        ).first()

    def _update_status(self, request):
        data = request.data
        order = self._get_order(request, data.get('order_id'))
        status = data.get('status', '')
        if not order or status not in ORDER_STATUSES:
            return err('Invalid parameters.')
        if status in STOCK_CHECK_STATUSES:
            stock_err = _check_stock_for_order(order)
            if stock_err:
                return err(f"Cannot move to '{status}': {stock_err}")
        order.status = status
        order.save(update_fields=['status', 'updated_at'])
        if status in DEDUCT_STATUSES:
            _deduct_stock_for_order(order, request.user)
        elif status == 'cancelled' and order.stock_deducted:
            _revert_stock_for_order(order, request.user)
        return ok(message='Order status updated successfully!')

    def _update_payment_status(self, request):
        data = request.data
        order = self._get_order(request, data.get('order_id'))
        payment_status = data.get('payment_status', '')
        if not order or payment_status not in PAYMENT_STATUSES:
            return err('Invalid parameters.')
        _sync_loyalty(order.id, payment_status)
        order.payment_status = payment_status
        order.save(update_fields=['payment_status', 'updated_at'])
        return ok(message='Payment status updated successfully!')

    def _update(self, request):
        data = request.data
        order = self._get_order(request, data.get('order_id'))
        name = (data.get('name') or '').strip()
        mobile = (data.get('mobile') or '').strip()
        if not order or not name or not mobile:
            return err('Order ID, name and mobile are required.')

        old_payment = order.payment_status
        payment_status = data.get('payment_status') or order.payment_status
        coupon_id = data.get('coupon_id')
        user_id = data.get('user_id')
        with transaction.atomic():
            order.name = name
            order.mobile = mobile
            order.address = (data.get('address') or '').strip()
            order.pincode = (data.get('pincode') or '').strip()
            order.status = data.get('status') or order.status
            order.payment_status = payment_status
            order.delivery_charge = _float(data.get('delivery_charge'))
            order.coupon_id = _int(coupon_id) if coupon_id else None
            order.user_id = _int(user_id) if user_id else None
            order.save()
            for item_id, qty in (data.get('item_quantities') or {}).items():
                qty = _int(qty)
                if qty > 0:
                    OrderItem.objects.filter(
                        id=_int(item_id), order_id=order.id,
                    ).update(quantity=qty)
                else:
                    OrderItem.objects.filter(id=_int(item_id), order_id=order.id).delete()

        if order.user_id and payment_status != old_payment:
            _sync_loyalty(order.id, payment_status)
        return ok(message='Order updated successfully.')

    def _add_item(self, request):
        data = request.data
        order = self._get_order(request, data.get('order_id'))
        flavor_id = _int(data.get('flavor_id'))
        quantity = _int(data.get('quantity'))
        if not order or flavor_id <= 0 or quantity <= 0:
            return err('Invalid parameters.')
        flavor = Flavor.objects.filter(
            id=flavor_id, brand_id=current_brand_id(request), is_active=True,
        ).first()
        if not flavor:
            return err('Flavor not found.')

        price_per_kg = flavor.price_per_kg
        sale_price_per_kg = flavor.sale_price_per_kg
        pack_id = _int(data.get('flavor_pack_id')) or None
        pack_label = (data.get('pack_label') or '').strip() or None
        if pack_id:
            pack = FlavorPack.objects.filter(id=pack_id, flavor_id=flavor.id).first()
            if pack:
                price_per_kg, sale_price_per_kg = _pack_prices(pack)

        item = OrderItem.objects.create(
            order=order, flavor=flavor, flavor_pack_id=pack_id,
            flavor_name=flavor.name, price_per_kg=price_per_kg,
            sale_price_per_kg=sale_price_per_kg, quantity=quantity,
            pack_label=pack_label,
        )
        return ok({
            'item': {
                'item_id': item.id,
                'name': flavor.name,
                'quantity': quantity,
                'sale_price_per_kg': sale_price_per_kg,
                'price_per_kg': price_per_kg,
                'item_total': quantity / 1000 * sale_price_per_kg,
                'pack_label': pack_label,
            },
        }, 'Item added.')

    def _remove_item(self, request):
        data = request.data
        order = self._get_order(request, data.get('order_id'))
        item_id = _int(data.get('item_id'))
        if not order or not item_id:
            return err('Invalid parameters.')
        deleted, _ignored = OrderItem.objects.filter(
            id=item_id, order_id=order.id,
        ).delete()
        return ok(message='Item removed.') if deleted else err('Item not found.')

    def _bulk_update(self, request):
        data = request.data
        ids = data.get('order_ids') or []
        status = data.get('status', '')
        if not ids or status not in ORDER_STATUSES[:6]:
            return err('No orders selected or invalid status.')

        orders = list(Order.objects.filter(
            id__in=ids, brand_id=current_brand_id(request),
        ))
        skipped = []
        if status == 'confirmed':
            valid = []
            for o in orders:
                if _check_stock_for_order(o):
                    skipped.append(o.id)
                else:
                    valid.append(o)
            orders = valid
            if not orders:
                return err('No orders could be confirmed due to insufficient stock.')

        for o in orders:
            o.status = status
            o.save(update_fields=['status', 'updated_at'])
            if status in DEDUCT_STATUSES:
                _deduct_stock_for_order(o, request.user)
            elif status == 'cancelled' and o.stock_deducted:
                _revert_stock_for_order(o, request.user)

        msg = f'{len(orders)} order(s) updated to {status}!'
        if skipped:
            msg += f' {len(skipped)} skipped due to insufficient stock.'
        return ok(message=msg)


class CreateOrderAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'orders'

    def post(self, request):
        brand_id = current_brand_id(request)
        data = request.data

        name = (data.get('name') or '').strip()
        mobile = (data.get('mobile') or '').strip()
        address = (data.get('address') or '').strip()
        pincode = (data.get('pincode') or '').strip()
        if not name or not mobile or not address or not pincode:
            return err('Required fields are missing.')

        delivery_charge = _float(data.get('delivery_charge'))
        coupon_code = (data.get('coupon_code') or '').strip()
        referral_code = (data.get('referral_code') or '').strip()

        user, _created = User.objects.get_or_create(
            brand_id=brand_id, mobile=mobile,
            defaults={'name': name, 'address': address, 'pincode': pincode},
        )

        items = []
        regular_total = 0.0
        sale_total = 0.0
        flavor_ids = []
        for raw in (data.get('items') or []):
            flavor_id = _int(raw.get('flavor_id'))
            quantity = _int(raw.get('quantity'))
            if flavor_id <= 0 or quantity <= 0:
                continue
            flavor = Flavor.objects.filter(id=flavor_id, brand_id=brand_id).first()
            if not flavor:
                continue
            price_per_kg = flavor.price_per_kg
            sale_price_per_kg = flavor.sale_price_per_kg
            pack_id = _int(raw.get('flavor_pack_id')) or None
            pack_label = (raw.get('pack_label') or '').strip() or None
            if pack_id:
                pack = FlavorPack.objects.filter(id=pack_id, flavor_id=flavor_id).first()
                if pack:
                    price_per_kg, sale_price_per_kg = _pack_prices(pack)
            regular_total += price_per_kg * quantity / 1000
            sale_total += sale_price_per_kg * quantity / 1000
            flavor_ids.append(flavor_id)
            items.append({
                'flavor_id': flavor_id, 'quantity': quantity,
                'price_per_kg': price_per_kg, 'sale_price_per_kg': sale_price_per_kg,
                'flavor_pack_id': pack_id, 'pack_label': pack_label,
                'flavor_name': flavor.name,
            })

        if not items:
            return err('No valid items in order.')

        coupon = None
        coupon_discount = 0.0
        if coupon_code:
            candidate = Coupon.objects.filter(
                brand_id=brand_id, code=coupon_code, is_active=True,
            ).first()
            if candidate:
                disc, cerr = _validate_coupon(candidate, sale_total, len(items), flavor_ids)
                if cerr is None:
                    coupon = candidate
                    coupon_discount = disc
                    sale_total -= disc

        referral_user = None
        if referral_code:
            referral_user = User.objects.filter(
                brand_id=brand_id, referral_code=referral_code,
            ).first()

        final_amount = sale_total + delivery_charge
        order_id = f'{(Brand.objects.filter(id=brand_id).values_list("order_prefix", flat=True).first() or "ORD")}_' + uuid4().hex[:13]

        with transaction.atomic():
            order = Order.objects.create(
                id=order_id, brand_id=brand_id, user=user,
                name=name, mobile=mobile, address=address, pincode=pincode,
                referral=referral_user, coupon=coupon, coupon_code=coupon_code,
                coupon_discount=coupon_discount, delivery_charge=delivery_charge,
                status='pending', payment_status='pending',
            )
            OrderItem.objects.bulk_create([
                OrderItem(
                    order=order, flavor_id=it['flavor_id'],
                    flavor_pack_id=it['flavor_pack_id'], flavor_name=it['flavor_name'],
                    price_per_kg=it['price_per_kg'], sale_price_per_kg=it['sale_price_per_kg'],
                    quantity=it['quantity'], pack_label=it['pack_label'],
                )
                for it in items
            ])

        return ok(
            {'order_id': order_id},
            f'Order created! ID: {order_id} | Total: ₹{final_amount:,.2f}',
        )


# ═════════════════════════════════════════════════════════ Coupons ══

@admin_login_required
@require_module('coupons')
@ensure_csrf_cookie
def coupons_page(request):
    return render(request, 'admin/coupons.html')


class CouponsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'coupons'

    def get(self, request):
        brand_id = current_brand_id(request)
        coupons = (
            Coupon.objects.filter(brand_id=brand_id)
            .annotate(
                total_uses=Count('orders'),
                total_discount_given=Coalesce(Sum('orders__coupon_discount'), 0.0),
            )
            .order_by('-created_at')
        )
        now = timezone.now()
        stats_qs = Coupon.objects.filter(brand_id=brand_id)
        stats = {
            'total_coupons': stats_qs.count(),
            'active_coupons': stats_qs.filter(is_active=True).count(),
            'valid_coupons': stats_qs.filter(
                is_active=True, start_date__lte=now, expiry_date__gt=now,
            ).count(),
            'expired_coupons': stats_qs.filter(expiry_date__lte=now).count(),
        }
        flavors = Flavor.objects.filter(
            brand_id=brand_id, is_active=True,
        ).order_by('name').values('id', 'name')
        return ok({
            'coupons': [self._coupon_dict(c) for c in coupons],
            'stats': stats,
            'flavors': list(flavors),
        })

    @staticmethod
    def _coupon_dict(c):
        return {
            'id': c.id,
            'code': c.code,
            'discount_amount': c.discount_amount,
            'discount_percentage': c.discount_percentage,
            'min_order_value': c.min_order_value,
            'min_quantity': c.min_quantity,
            'max_discount_amount': float(c.max_discount_amount) if c.max_discount_amount is not None else None,
            'max_uses': c.max_uses,
            'used_count': c.used_count,
            'valid_flavors': json.dumps(c.valid_flavors) if c.valid_flavors else None,
            'is_active': 1 if c.is_active else 0,
            'start_date': c.start_date.isoformat() if c.start_date else None,
            'expiry_date': c.expiry_date.isoformat() if c.expiry_date else None,
            'total_uses': c.total_uses,
            'total_discount_given': c.total_discount_given,
        }

    def post(self, request):
        handlers = {
            'add': self._add,
            'edit': self._edit,
            'toggle': self._toggle,
            'delete': self._delete,
        }
        handler = handlers.get(request.data.get('action', ''))
        if not handler:
            return err('Unknown action.')
        return handler(request)

    @staticmethod
    def _coupon_fields(data):
        valid_flavors = data.get('valid_flavors')
        return {
            'code': (data.get('code') or '').strip().upper(),
            'discount_amount': _float(data.get('discount_amount')) if data.get('discount_amount') else None,
            'discount_percentage': _int(data.get('discount_percentage')) if data.get('discount_percentage') else None,
            'min_order_value': _float(data.get('min_order_value')),
            'min_quantity': _float(data.get('min_quantity')),
            'max_discount_amount': _float(data.get('max_discount_amount')) if data.get('max_discount_amount') else None,
            'max_uses': _int(data.get('max_uses')) if data.get('max_uses') else None,
            'valid_flavors': valid_flavors if valid_flavors else None,
            'start_date': _parse_dt(data.get('start_date')),
            'expiry_date': _parse_dt(data.get('expiry_date')),
        }

    def _add(self, request):
        fields = self._coupon_fields(request.data)
        if not fields['code']:
            return err('Coupon code is required.')
        try:
            coupon = Coupon.objects.create(
                brand_id=current_brand_id(request), **fields,
            )
        except IntegrityError:
            return err('Error adding coupon. Code may already exist.')
        return ok({'id': coupon.id}, 'Coupon added successfully!')

    def _edit(self, request):
        coupon = Coupon.objects.filter(
            id=_int(request.data.get('id')), brand_id=current_brand_id(request),
        ).first()
        fields = self._coupon_fields(request.data)
        if not coupon or not fields['code']:
            return err('Invalid parameters.')
        for key, value in fields.items():
            setattr(coupon, key, value)
        try:
            coupon.save()
        except IntegrityError:
            return err('Error updating coupon. Code may already exist.')
        return ok(message='Coupon updated successfully!')

    def _toggle(self, request):
        coupon = Coupon.objects.filter(
            id=_int(request.data.get('id')), brand_id=current_brand_id(request),
        ).first()
        if not coupon:
            return err('Error updating coupon.')
        coupon.is_active = not coupon.is_active
        coupon.save(update_fields=['is_active', 'updated_at'])
        return ok(message='Coupon status updated!')

    def _delete(self, request):
        coupon = Coupon.objects.filter(
            id=_int(request.data.get('id')), brand_id=current_brand_id(request),
        ).first()
        if not coupon:
            return err('Error deleting coupon.')
        coupon.delete()
        return ok(message='Coupon deleted successfully!')
