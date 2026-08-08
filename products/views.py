"""
Products admin slice: Flavors (+ packs) and Stocks (batches, movements,
alerts, vendors). Ported from the PHP `admin/flavors.php` /
`admin/api/flavors.php` and `admin/stocks.php` / `admin/api/stocks.php`.

Each API mirrors its PHP counterpart: a single endpoint serves GET
(list/detail, dispatched on an `action` query param) and POST (dispatched on
an `action` field in the JSON body). Every query is scoped to the active brand
via the flavor's brand (vendors are global, matching the PHP schema).
"""
import json

from django.db import IntegrityError
from django.db.models import Count, F, FloatField, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from orders.models import OrderItem
from products.models import (
    Flavor,
    FlavorPack,
    Stock,
    StockAlert,
    StockMovement,
    Vendor,
)

REVENUE = F('quantity') * F('sale_price_per_kg') / 1000.0


def _grams_to_kg(grams):
    return round((grams or 0) / 1000, 3)


def _kg_to_grams(kg):
    return int(round(float(kg or 0) * 1000))


def _parse_nutrition(raw):
    """JS sends nutrition facts as a JSON string; store a dict or None."""
    if isinstance(raw, dict):
        return raw or None
    text = (str(raw) if raw is not None else '').strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return None
    return value or None


def _nutrition_out(value):
    """Model stores a dict/None; the JS expects a JSON string to parse."""
    if not value:
        return ''
    return json.dumps(value)


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


# ═══════════════════════════════════════════════════════ Flavors ══

@admin_login_required
@require_module('flavors')
@ensure_csrf_cookie
def flavors_page(request):
    return render(request, 'admin/flavors.html')


class FlavorsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'flavors'

    def get(self, request):
        brand_id = current_brand_id(request)

        packs_for = request.query_params.get('flavor_packs')
        if packs_for:
            packs = FlavorPack.objects.filter(
                flavor_id=packs_for, flavor__brand_id=brand_id,
            ).order_by('weight_grams')
            return ok([self._pack_dict(p) for p in packs])

        flavors = list(
            Flavor.objects.filter(brand_id=brand_id)
            .prefetch_related('packs')
            .order_by('name')
        )
        flavor_ids = [f.id for f in flavors]

        sold = {
            row['flavor_id']: row
            for row in OrderItem.objects.filter(
                order__brand_id=brand_id,
                order__status='delivered',
                flavor_id__in=flavor_ids,
            )
            .values('flavor_id')
            .annotate(
                qty=Sum('quantity'),
                orders=Count('order', distinct=True),
                rev=Sum(REVENUE, output_field=FloatField()),
            )
        }

        data = [self._flavor_dict(f, sold.get(f.id)) for f in flavors]
        return ok({'flavors': data, 'stats': self._stats(brand_id)})

    @staticmethod
    def _stats(brand_id):
        qs = Flavor.objects.filter(brand_id=brand_id)
        agg = qs.aggregate(
            total_flavors=Count('id'),
            active_flavors=Count('id', filter=Q(is_active=True)),
            avg_price=Sum('sale_price_per_kg') / Count('id') if False else Coalesce(Sum('sale_price_per_kg'), 0.0),
        )
        prices = list(qs.values_list('sale_price_per_kg', flat=True))
        avg_price = round(sum(prices) / len(prices)) if prices else 0
        return {
            'total_flavors': agg['total_flavors'],
            'active_flavors': agg['active_flavors'],
            'avg_price': avg_price,
            'max_price': max(prices) if prices else 0,
            'min_price': min(prices) if prices else 0,
        }

    @staticmethod
    def _pack_dict(p):
        return {
            'id': p.id,
            'flavor_id': p.flavor_id,
            'weight_grams': p.weight_grams,
            'label': p.label,
            'mrp': p.mrp,
            'selling_price': p.selling_price,
            'cost_price': p.cost_price,
            'sku': p.sku or '',
            'is_active': 1 if p.is_active else 0,
        }

    def _flavor_dict(self, f, sold):
        return {
            'id': f.id,
            'name': f.name,
            'description': f.description or '',
            'price_per_kg': f.price_per_kg,
            'sale_price_per_kg': f.sale_price_per_kg,
            'reorder_level_grams': f.reorder_level_grams,
            'is_active': 1 if f.is_active else 0,
            'image': f.image.url if f.image else '',
            'ingredients': f.ingredients or '',
            'nutrition_fact': _nutrition_out(f.nutrition_fact),
            'total_orders': (sold or {}).get('orders', 0) or 0,
            'total_quantity_sold': round(((sold or {}).get('qty', 0) or 0) / 1000, 2),
            'total_revenue': float((sold or {}).get('rev', 0) or 0),
            'packs': [self._pack_dict(p) for p in f.packs.all()],
        }

    def post(self, request):
        action = request.data.get('action', '')
        handlers = {
            'create': self._create, 'add': self._create,
            'update': self._update, 'edit': self._update,
            'delete': self._delete,
            'toggle': self._toggle,
            'add_pack': self._add_pack,
            'update_pack': self._update_pack, 'edit_pack': self._update_pack,
            'delete_pack': self._delete_pack,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request)

    @staticmethod
    def _flavor_fields(data):
        return {
            'name': (data.get('name') or '').strip(),
            'description': (data.get('description') or '').strip(),
            'price_per_kg': _float(data.get('price_per_kg')),
            'sale_price_per_kg': _float(data.get('sale_price_per_kg')),
            'reorder_level_grams': _kg_to_grams(data.get('reorder_level_kg') or 5),
            'is_active': _int(data.get('is_active', 1)) == 1,
            'ingredients': (data.get('ingredients') or '').strip() or None,
            'nutrition_fact': _parse_nutrition(data.get('nutrition_fact')),
        }

    def _create(self, request):
        brand_id = current_brand_id(request)
        fields = self._flavor_fields(request.data)
        if not fields['name']:
            return err('Flavor name is required.')
        image = request.FILES.get('image')
        if image:
            fields['image'] = image
        flavor = Flavor.objects.create(brand_id=brand_id, **fields)
        return ok({'id': flavor.id}, 'Flavor added successfully!')

    def _update(self, request):
        brand_id = current_brand_id(request)
        flavor = Flavor.objects.filter(
            id=_int(request.data.get('id')), brand_id=brand_id,
        ).first()
        if not flavor:
            return err('Flavor not found.', status=404)
        for key, value in self._flavor_fields(request.data).items():
            setattr(flavor, key, value)
        # Image: replace only when a new file is uploaded; clear on explicit
        # remove; otherwise keep the existing one untouched.
        image = request.FILES.get('image')
        if image:
            flavor.image = image
        elif _int(request.data.get('remove_image')) == 1:
            flavor.image = None
        flavor.save()
        return ok(message='Flavor updated successfully!')

    def _delete(self, request):
        brand_id = current_brand_id(request)
        flavor = Flavor.objects.filter(
            id=_int(request.data.get('id')), brand_id=brand_id,
        ).first()
        if not flavor:
            return err('Flavor not found.', status=404)
        if OrderItem.objects.filter(flavor_id=flavor.id).exists():
            return err('Cannot delete: flavor used in orders. Deactivate instead.')
        flavor.delete()
        return ok(message='Flavor deleted successfully!')

    def _toggle(self, request):
        brand_id = current_brand_id(request)
        flavor = Flavor.objects.filter(
            id=_int(request.data.get('id')), brand_id=brand_id,
        ).first()
        if not flavor:
            return err('Flavor not found.', status=404)
        flavor.is_active = not flavor.is_active
        flavor.save(update_fields=['is_active', 'updated_at'])
        return ok(message='Flavor status updated!')

    def _add_pack(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        flavor = Flavor.objects.filter(
            id=_int(data.get('flavor_id')), brand_id=brand_id,
        ).first()
        weight = _int(data.get('weight_grams'))
        label = (data.get('label') or '').strip()
        if not flavor or not weight or not label:
            return err('Flavor, weight and label are required.')
        try:
            pack = FlavorPack.objects.create(
                flavor=flavor, weight_grams=weight, label=label,
                mrp=_float(data.get('mrp')), selling_price=_float(data.get('selling_price')),
                cost_price=_float(data.get('cost_price')),
                sku=(data.get('sku') or '').strip() or None,
            )
        except IntegrityError:
            return err('Error adding pack.')
        return ok({'id': pack.id}, 'Pack added!')

    def _update_pack(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        pack = FlavorPack.objects.filter(
            id=_int(data.get('id')), flavor__brand_id=brand_id,
        ).first()
        if not pack:
            return err('Pack not found.', status=404)
        pack.weight_grams = _int(data.get('weight_grams'))
        pack.label = (data.get('label') or '').strip()
        pack.mrp = _float(data.get('mrp'))
        pack.selling_price = _float(data.get('selling_price'))
        pack.cost_price = _float(data.get('cost_price'))
        pack.sku = (data.get('sku') or '').strip() or None
        pack.is_active = _int(data.get('is_active', 1)) == 1
        try:
            pack.save()
        except IntegrityError:
            return err('Error updating pack.')
        return ok(message='Pack updated!')

    def _delete_pack(self, request):
        brand_id = current_brand_id(request)
        pack = FlavorPack.objects.filter(
            id=_int(request.data.get('id')), flavor__brand_id=brand_id,
        ).first()
        if not pack:
            return err('Pack not found.', status=404)
        pack.delete()
        return ok(message='Pack deleted!')


# ════════════════════════════════════════════════════════ Stocks ══

def _stock_status(qty, reserved, reorder):
    if qty <= 0:
        return 'out'
    if (qty - reserved) <= 0:
        return 'critical'
    if reorder > 0 and qty <= reorder:
        return 'low'
    return 'good'


def _refresh_flavor(flavor):
    """
    Mirror the PHP `refreshFlavor`: auto-activate the next FIFO batch if the
    active one has emptied, then regenerate unacknowledged low/out/expiry
    alerts for the flavor.
    """
    active = flavor.stock_batches.filter(is_active_batch=True).first()
    if active and active.quantity_grams <= 0:
        active.is_active_batch = False
        active.save(update_fields=['is_active_batch'])
        nxt = (
            flavor.stock_batches
            .filter(is_active_batch=False, quantity_grams__gt=0)
            .order_by('last_restocked_date')
            .first()
        )
        if nxt:
            nxt.is_active_batch = True
            nxt.save(update_fields=['is_active_batch'])

    flavor.alerts.filter(is_acknowledged=False).delete()

    agg = flavor.stock_batches.aggregate(total=Coalesce(Sum('quantity_grams'), 0))
    total_qty = agg['total'] or 0
    reorder = flavor.reorder_level_grams or 0
    earliest_expiry = (
        flavor.stock_batches.filter(expiry_date__isnull=False)
        .order_by('expiry_date')
        .values_list('expiry_date', flat=True)
        .first()
    )

    def _make(alert_type):
        StockAlert.objects.create(
            flavor=flavor, alert_type=alert_type,
            current_quantity_grams=total_qty, reorder_level_grams=reorder,
            expiry_date=earliest_expiry,
        )

    if total_qty <= 0:
        _make(StockAlert.AlertType.OUT_OF_STOCK)
    elif reorder > 0 and total_qty <= reorder:
        _make(StockAlert.AlertType.LOW_STOCK)

    if earliest_expiry:
        days = (earliest_expiry - timezone.localdate()).days
        if days < 0:
            _make(StockAlert.AlertType.EXPIRED)
        elif days <= 30:
            _make(StockAlert.AlertType.EXPIRING_SOON)


@admin_login_required
@require_module('stocks')
@ensure_csrf_cookie
def stocks_page(request):
    return render(request, 'admin/stocks.html')


class StocksAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'stocks'

    # ── GET dispatch ──
    def get(self, request):
        action = request.query_params.get('action', 'list')
        handlers = {
            'list': self._list,
            'batches': self._batches,
            'movements': self._movements,
            'alerts': self._alerts,
            'stats': self._stats,
            'vendors': self._vendors,
            'generate_batch_code': self._generate_batch_code,
            'check_vendor_code': self._check_vendor_code,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request)

    def _list(self, request):
        brand_id = current_brand_id(request)
        rows = (
            Stock.objects.filter(flavor__brand_id=brand_id)
            .select_related('flavor', 'vendor')
            .order_by('flavor__name', '-is_active_batch', '-last_restocked_date')
        )
        return ok([self._batch_row(s) for s in rows])

    @staticmethod
    def _batch_row(s):
        reorder = s.flavor.reorder_level_grams or 0
        return {
            'id': s.id,
            'flavor_id': s.flavor_id,
            'flavor_name': s.flavor.name,
            'batch_number': s.batch_number or '',
            'is_active_batch': 1 if s.is_active_batch else 0,
            'quantity_grams': s.quantity_grams,
            'reserved_quantity_grams': s.reserved_quantity_grams,
            'quantity_kg': _grams_to_kg(s.quantity_grams),
            'reserved_kg': _grams_to_kg(s.reserved_quantity_grams),
            'available_kg': _grams_to_kg(s.quantity_grams - s.reserved_quantity_grams),
            'reorder_level_grams': reorder,
            'reorder_level_kg': _grams_to_kg(reorder),
            'status': _stock_status(s.quantity_grams, s.reserved_quantity_grams, reorder),
            'vendor_name': s.vendor.name if s.vendor else '',
            'supplier_name': s.supplier_name or '',
            'expiry_date': s.expiry_date.isoformat() if s.expiry_date else '',
            'last_restocked_date': s.last_restocked_date.isoformat() if s.last_restocked_date else '',
            'storage_location': s.storage_location or '',
            'notes': s.notes or '',
        }

    def _batches(self, request):
        brand_id = current_brand_id(request)
        flavor_id = _int(request.query_params.get('flavor_id'))
        if not flavor_id:
            return err('flavor_id required.')
        rows = (
            Stock.objects.filter(
                flavor_id=flavor_id, flavor__brand_id=brand_id, quantity_grams__gt=0,
            )
            .select_related('vendor')
            .order_by('-is_active_batch', '-last_restocked_date')
        )
        return ok([
            {
                'id': s.id,
                'batch_number': s.batch_number or '',
                'is_active_batch': 1 if s.is_active_batch else 0,
                'quantity_grams': s.quantity_grams,
                'reserved_quantity_grams': s.reserved_quantity_grams,
                'available_kg': _grams_to_kg(s.quantity_grams - s.reserved_quantity_grams),
                'expiry_date': s.expiry_date.isoformat() if s.expiry_date else '',
                'storage_location': s.storage_location or '',
                'vendor_name': s.vendor.name if s.vendor else '',
            }
            for s in rows
        ])

    def _movements(self, request):
        brand_id = current_brand_id(request)
        params = request.query_params
        page = max(1, _int(params.get('page'), 1))
        per_page = _int(params.get('per_page'), 20)
        if per_page not in (10, 20, 50):
            per_page = 20
        offset = (page - 1) * per_page

        qs = StockMovement.objects.filter(flavor__brand_id=brand_id)
        flavor_id = _int(params.get('flavor_id'))
        if flavor_id:
            qs = qs.filter(flavor_id=flavor_id)
        if params.get('movement_type'):
            qs = qs.filter(movement_type=params.get('movement_type'))
        if params.get('reference_type'):
            qs = qs.filter(reference_type=params.get('reference_type'))

        total = qs.count()
        rows = (
            qs.select_related('flavor', 'stock', 'stock__vendor', 'created_by')
            .order_by('-created_at')[offset:offset + per_page]
        )
        items = [
            {
                'id': m.id,
                'created_at': m.created_at.isoformat(),
                'flavor_name': m.flavor.name,
                'batch_number': m.stock.batch_number if m.stock else '',
                'vendor_name': m.stock.vendor.name if (m.stock and m.stock.vendor) else '',
                'movement_type': m.movement_type,
                'reference_type': m.reference_type,
                'reference_id': m.reference_id or '',
                'quantity_grams': m.quantity_grams,
                'quantity_kg': _grams_to_kg(m.quantity_grams),
                'previous_quantity_kg': _grams_to_kg(m.previous_quantity_grams),
                'new_quantity_kg': _grams_to_kg(m.new_quantity_grams),
                'notes': m.notes or '',
                'created_by_name': m.created_by.name if m.created_by else '',
            }
            for m in rows
        ]
        return ok({'items': items, 'total': total, 'page': page, 'per_page': per_page})

    def _alerts(self, request):
        brand_id = current_brand_id(request)
        rows = (
            StockAlert.objects.filter(is_acknowledged=False, flavor__brand_id=brand_id)
            .select_related('flavor')
            .order_by('-created_at')
        )
        return ok([
            {
                'id': a.id,
                'flavor_name': a.flavor.name,
                'alert_type': a.alert_type,
                'current_quantity_grams': a.current_quantity_grams,
                'current_quantity_kg': _grams_to_kg(a.current_quantity_grams),
                'reorder_level_grams': a.reorder_level_grams,
                'reorder_level_kg': _grams_to_kg(a.reorder_level_grams),
                'expiry_date': a.expiry_date.isoformat() if a.expiry_date else '',
                'created_at': a.created_at.isoformat(),
            }
            for a in rows
        ])

    def _stats(self, request):
        brand_id = current_brand_id(request)
        batches = list(
            Stock.objects.filter(flavor__brand_id=brand_id)
            .values('flavor_id', 'quantity_grams', 'flavor__reorder_level_grams')
        )
        total_grams = sum(b['quantity_grams'] for b in batches)
        flavors_tracked = len({b['flavor_id'] for b in batches})
        out_of_stock = len({b['flavor_id'] for b in batches if b['quantity_grams'] <= 0})
        low_stock = sum(
            1 for b in batches
            if b['quantity_grams'] > 0
            and (b['flavor__reorder_level_grams'] or 0) > 0
            and b['quantity_grams'] <= b['flavor__reorder_level_grams']
        )
        today = timezone.localdate()
        today_movements = StockMovement.objects.filter(
            flavor__brand_id=brand_id, created_at__date=today,
        ).count()
        active_alerts = StockAlert.objects.filter(
            is_acknowledged=False, flavor__brand_id=brand_id,
        ).count()
        return ok({
            'total_kg': _grams_to_kg(total_grams),
            'flavors_tracked': flavors_tracked,
            'total_batches': len(batches),
            'out_of_stock': out_of_stock,
            'low_stock': low_stock,
            'today_movements': today_movements,
            'active_alerts': active_alerts,
        })

    def _vendors(self, request):
        rows = (
            Vendor.objects.annotate(
                batch_count=Count('stock_batches', filter=Q(stock_batches__quantity_grams__gt=0)),
                total_stock_grams=Coalesce(
                    Sum('stock_batches__quantity_grams', filter=Q(stock_batches__quantity_grams__gt=0)), 0,
                ),
            )
            .order_by('name')
        )
        return ok([
            {
                'id': v.id,
                'name': v.name,
                'code': v.code or '',
                'fssai_number': v.fssai_number or '',
                'contact_name': v.contact_name or '',
                'phone': v.phone or '',
                'email': v.email or '',
                'address': v.address or '',
                'notes': v.notes or '',
                'is_active': 1 if v.is_active else 0,
                'batch_count': v.batch_count,
                'total_stock_grams': v.total_stock_grams,
                'total_stock_kg': _grams_to_kg(v.total_stock_grams),
            }
            for v in rows
        ])

    def _generate_batch_code(self, request):
        vendor_id = _int(request.query_params.get('vendor_id'))
        vendor = Vendor.objects.filter(id=vendor_id).first()
        if not vendor or not vendor.code:
            return err('Vendor has no code set.')
        today = timezone.localdate()
        seq = Stock.objects.filter(vendor_id=vendor_id, created_at__date=today).count() + 1
        code = f'{vendor.code.upper()}{today.strftime("%y")}{today.strftime("%m")}{seq}'
        return ok({'batch_code': code})

    def _check_vendor_code(self, request):
        code = (request.query_params.get('code') or '').strip().upper()
        exclude_id = _int(request.query_params.get('exclude_id'))
        if not code:
            return ok({'available': False})
        taken = Vendor.objects.filter(code=code).exclude(id=exclude_id).exists()
        return ok({'available': not taken})

    # ── POST dispatch ──
    def post(self, request):
        action = request.data.get('action', '')
        handlers = {
            'restock': self._restock,
            'record_movement': self._record_movement, 'adjust': self._record_movement,
            'set_active_batch': self._set_active_batch,
            'update_settings': self._update_settings,
            'acknowledge_alert': self._acknowledge_alert,
            'delete_stock': self._delete_stock,
            'delete_movement': self._delete_movement,
            'create_vendor': self._create_vendor,
            'update_vendor': self._update_vendor,
            'toggle_vendor': self._toggle_vendor,
            'delete_vendor': self._delete_vendor,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request)

    def _restock(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        flavor = Flavor.objects.filter(
            id=_int(data.get('flavor_id')), brand_id=brand_id,
        ).first()
        qty_kg = _float(data.get('quantity_kg'))
        if not flavor or qty_kg <= 0:
            return err('Flavor and quantity are required.')

        qty_grams = _kg_to_grams(qty_kg)
        cost_price = _float(data.get('cost_price_per_kg'))
        vendor_raw = str(data.get('vendor_id') or '').strip()
        vendor_id = _int(vendor_raw) if vendor_raw else None
        is_first_batch = not flavor.stock_batches.filter(is_active_batch=True).exists()

        batch = Stock.objects.create(
            flavor=flavor,
            quantity_grams=qty_grams,
            is_active_batch=is_first_batch,
            cost_price_per_kg=cost_price or None,
            vendor_id=vendor_id,
            supplier_name=(data.get('supplier_name') or '').strip(),
            supplier_contact=(data.get('supplier_contact') or '').strip(),
            batch_number=(data.get('batch_number') or '').strip(),
            expiry_date=(data.get('expiry_date') or '').strip() or None,
            storage_location=(data.get('storage_location') or '').strip(),
            notes=(data.get('notes') or '').strip(),
            last_restocked_date=timezone.localdate(),
        )
        StockMovement.objects.create(
            flavor=flavor, stock=batch,
            movement_type=StockMovement.MovementType.IN,
            quantity_grams=qty_grams, previous_quantity_grams=0, new_quantity_grams=qty_grams,
            reference_type=StockMovement.ReferenceType.PURCHASE,
            cost_price_per_kg=cost_price or None,
            notes=(data.get('notes') or '').strip(),
            created_by=request.user,
        )
        _refresh_flavor(flavor)
        message = 'Stock batch added!' + (' Set as active batch.' if is_first_batch else '')
        return ok(message=message)

    def _record_movement(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        ref_type = data.get('reference_type', '')
        qty_kg = _float(data.get('quantity_kg'))
        batch = Stock.objects.filter(
            id=_int(data.get('stock_id')), flavor__brand_id=brand_id,
        ).select_related('flavor').first()
        if not batch or ref_type not in ('sale', 'wastage', 'adjustment') or qty_kg <= 0:
            return err('Invalid parameters.')

        qty_grams = _kg_to_grams(qty_kg)
        prev = batch.quantity_grams
        new_qty = max(0, prev - qty_grams)
        mov_type = (
            StockMovement.MovementType.ADJUSTMENT if ref_type == 'adjustment'
            else StockMovement.MovementType.OUT
        )
        batch.quantity_grams = new_qty
        batch.save(update_fields=['quantity_grams', 'updated_at'])
        StockMovement.objects.create(
            flavor=batch.flavor, stock=batch, movement_type=mov_type,
            quantity_grams=qty_grams, previous_quantity_grams=prev, new_quantity_grams=new_qty,
            reference_type=ref_type,
            reference_id=(data.get('reference_id') or '').strip(),
            notes=(data.get('notes') or '').strip(),
            created_by=request.user,
        )
        _refresh_flavor(batch.flavor)
        return ok(message='Movement recorded.')

    def _set_active_batch(self, request):
        brand_id = current_brand_id(request)
        batch = Stock.objects.filter(
            id=_int(request.data.get('stock_id')), flavor__brand_id=brand_id,
        ).first()
        if not batch:
            return err('Batch not found.')
        if batch.quantity_grams <= 0:
            return err('Cannot activate an empty batch.')
        Stock.objects.filter(flavor_id=batch.flavor_id).update(is_active_batch=False)
        batch.is_active_batch = True
        batch.save(update_fields=['is_active_batch'])
        return ok(message='Active batch updated.')

    def _update_settings(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        batch = Stock.objects.filter(
            id=_int(data.get('stock_id')), flavor__brand_id=brand_id,
        ).first()
        if not batch:
            return err('Invalid batch.')
        batch.storage_location = (data.get('storage_location') or '').strip()
        batch.notes = (data.get('notes') or '').strip()
        batch.save(update_fields=['storage_location', 'notes', 'updated_at'])
        return ok(message='Batch settings updated.')

    def _acknowledge_alert(self, request):
        brand_id = current_brand_id(request)
        alert = StockAlert.objects.filter(
            id=_int(request.data.get('id')), flavor__brand_id=brand_id,
        ).first()
        if not alert:
            return err('Invalid ID.')
        alert.is_acknowledged = True
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.save(update_fields=['is_acknowledged', 'acknowledged_by', 'acknowledged_at'])
        return ok(message='Alert dismissed.')

    def _delete_stock(self, request):
        brand_id = current_brand_id(request)
        batch = Stock.objects.filter(
            id=_int(request.data.get('stock_id')), flavor__brand_id=brand_id,
        ).select_related('flavor').first()
        if not batch:
            return err('Batch not found.')
        if batch.reserved_quantity_grams > 0:
            return err('Cannot delete batch — it has reserved stock against active orders.')
        flavor = batch.flavor
        StockMovement.objects.filter(stock_id=batch.id).delete()
        batch.delete()
        _refresh_flavor(flavor)
        return ok(message='Batch and its movements deleted.')

    def _delete_movement(self, request):
        brand_id = current_brand_id(request)
        movement = StockMovement.objects.filter(
            id=_int(request.data.get('id')), flavor__brand_id=brand_id,
        ).first()
        if not movement:
            return err('Invalid movement ID.')
        movement.delete()
        return ok(message='Movement deleted.')

    @staticmethod
    def _vendor_fields(data):
        return {
            'name': (data.get('name') or '').strip(),
            'code': (data.get('code') or '').strip().upper(),
            'fssai_number': (data.get('fssai_number') or '').strip(),
            'contact_name': (data.get('contact_name') or '').strip(),
            'phone': (data.get('phone') or '').strip(),
            'email': (data.get('email') or '').strip(),
            'address': (data.get('address') or '').strip(),
            'notes': (data.get('notes') or '').strip(),
        }

    def _create_vendor(self, request):
        fields = self._vendor_fields(request.data)
        if not fields['name'] or not fields['code']:
            return err('Name and code are required.')
        try:
            vendor = Vendor.objects.create(**fields)
        except IntegrityError:
            return err(f"Vendor code '{fields['code']}' is already taken.")
        return ok({'id': vendor.id}, 'Vendor created!')

    def _update_vendor(self, request):
        vendor = Vendor.objects.filter(id=_int(request.data.get('id'))).first()
        fields = self._vendor_fields(request.data)
        if not vendor or not fields['name'] or not fields['code']:
            return err('Name and code are required.')
        for key, value in fields.items():
            setattr(vendor, key, value)
        try:
            vendor.save()
        except IntegrityError:
            return err(f"Vendor code '{fields['code']}' is already taken.")
        return ok(message='Vendor updated.')

    def _toggle_vendor(self, request):
        vendor = Vendor.objects.filter(id=_int(request.data.get('id'))).first()
        if not vendor:
            return err('Invalid vendor ID.')
        vendor.is_active = not vendor.is_active
        vendor.save(update_fields=['is_active', 'updated_at'])
        return ok(message='Vendor updated.')

    def _delete_vendor(self, request):
        vendor = Vendor.objects.filter(id=_int(request.data.get('id'))).first()
        if not vendor:
            return err('Invalid vendor ID.')
        if Stock.objects.filter(vendor_id=vendor.id).exists():
            return err('Cannot delete vendor — inventory batches are linked to them. Remove the inventory first.')
        vendor.delete()
        return ok(message='Vendor deleted.')
