"""
Packs admin slice: standalone brand-scoped Pack management.

Split out of FlavorsAPI so Flavors (a global catalog, ported to the General
nav section) and Packs (brand-wise pricing/packaging, its own top-menu page)
are managed on separate pages with separate module permissions. See
products/views.py for the Flavor/Stock APIs.
"""
from django.db import IntegrityError
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from products.models import Flavor, FlavorPack


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


@admin_login_required
@require_module('packs')
@ensure_csrf_cookie
def packs_page(request):
    return render(request, 'admin/packs.html')


class PacksAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'packs'

    # ── GET dispatch ──
    def get(self, request):
        action = request.query_params.get('action', 'list')
        handlers = {
            'list': self._list,
            'flavor_options': self._flavor_options,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request)

    def _list(self, request):
        brand_id = current_brand_id(request)
        packs = (
            FlavorPack.objects.filter(brand_id=brand_id)
            .select_related('flavor')
            .order_by('flavor__name', 'weight_grams')
        )
        return ok([self._pack_dict(p) for p in packs])

    def _flavor_options(self, request):
        flavors = (
            Flavor.objects.filter(is_active=True)
            .order_by('name')
            .values('id', 'name')
        )
        return ok(list(flavors))

    @staticmethod
    def _pack_dict(p):
        return {
            'id': p.id,
            'flavor_id': p.flavor_id,
            'flavor_name': p.flavor.name,
            'weight_grams': p.weight_grams,
            'label': p.label,
            'mrp': p.mrp,
            'selling_price': p.selling_price,
            'cost_price': p.cost_price,
            'sku': p.sku or '',
            'is_active': 1 if p.is_active else 0,
        }

    # ── POST dispatch ──
    def post(self, request):
        action = request.data.get('action', '')
        handlers = {
            'create': self._create, 'add': self._create,
            'update': self._update, 'edit': self._update,
            'delete': self._delete,
            'toggle': self._toggle,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request)

    def _create(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        flavor = Flavor.objects.filter(id=_int(data.get('flavor_id'))).first()
        weight = _int(data.get('weight_grams'))
        label = (data.get('label') or '').strip()
        if not flavor or not weight or not label:
            return err('Flavor, weight and label are required.')
        try:
            pack = FlavorPack.objects.create(
                flavor=flavor, brand_id=brand_id, weight_grams=weight, label=label,
                mrp=_float(data.get('mrp')), selling_price=_float(data.get('selling_price')),
                cost_price=_float(data.get('cost_price')),
                sku=(data.get('sku') or '').strip() or None,
                is_active=_int(data.get('is_active', 1)) == 1,
            )
        except IntegrityError:
            return err('A pack with this label already exists for this brand.')
        return ok({'id': pack.id}, 'Pack added!')

    def _update(self, request):
        brand_id = current_brand_id(request)
        data = request.data
        pack = FlavorPack.objects.filter(
            id=_int(data.get('id')), brand_id=brand_id,
        ).first()
        if not pack:
            return err('Pack not found.', status=404)
        flavor_id = _int(data.get('flavor_id'))
        if flavor_id:
            flavor = Flavor.objects.filter(id=flavor_id).first()
            if not flavor:
                return err('Flavor not found.', status=404)
            pack.flavor = flavor
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
            return err('A pack with this label already exists for this brand.')
        return ok(message='Pack updated!')

    def _delete(self, request):
        brand_id = current_brand_id(request)
        pack = FlavorPack.objects.filter(
            id=_int(request.data.get('id')), brand_id=brand_id,
        ).first()
        if not pack:
            return err('Pack not found.', status=404)
        pack.delete()
        return ok(message='Pack deleted!')

    def _toggle(self, request):
        brand_id = current_brand_id(request)
        pack = FlavorPack.objects.filter(
            id=_int(request.data.get('id')), brand_id=brand_id,
        ).first()
        if not pack:
            return err('Pack not found.', status=404)
        pack.is_active = not pack.is_active
        pack.save(update_fields=['is_active', 'updated_at'])
        return ok(message='Pack status updated!')
