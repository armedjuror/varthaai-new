"""
Brands slice (ported from PHP `admin/brands.php` + `admin/api/brands.php`).

super_admin only. Stocks and expenses are shared across brands; everything
else is per-brand. GET keeps the PHP shape `{success, data, current_brand_id}`
(the ported inline JS reads `res.data`); POST actions return HTTP 200
`{success, message}` so the jQuery `.done()` handler surfaces the message.

Brand *switching* is handled elsewhere (`accounts:brand_switch` + the base.html
topbar switcher), so this API only covers CRUD: add / edit / toggle / delete.
"""
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import User
from core.api import HasModulePermission, current_brand_id
from core.auth import admin_login_required
from core.models import Brand
from orders.models import Order


@admin_login_required
@ensure_csrf_cookie
def brands_page(request):
    if not getattr(request.user, 'is_super_admin', False):
        return redirect('core:dashboard')
    return render(request, 'admin/brands.html')


def _fail(message):
    """Business failure: HTTP 200 so the inline JS `res.success` check runs."""
    return Response({'success': False, 'message': message})


def _clean(body, key):
    return (body.get(key) or '').strip()


class BrandsAPI(APIView):
    permission_classes = [HasModulePermission]  # any logged-in admin may GET

    def get(self, request):
        data = [
            {
                'id': b.id,
                'name': b.name,
                'logo': b.logo.name if b.logo else '',
                'website': b.website,
                'instagram': b.instagram,
                'order_prefix': b.order_prefix,
                'is_active': b.is_active,
            }
            for b in Brand.objects.order_by('id')
        ]
        return Response({
            'success': True,
            'data': data,
            'current_brand_id': current_brand_id(request),
        })

    def post(self, request):
        if not getattr(request.user, 'is_super_admin', False):
            return _fail('Super admin only.')
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        if action == 'add':
            return self._add(body)
        if action == 'edit':
            return self._edit(request, body)
        if action == 'toggle':
            return self._toggle(body)
        if action == 'delete':
            return self._delete(body)
        return _fail('Unknown action.')

    def _add(self, body):
        name = _clean(body, 'name')
        order_prefix = _clean(body, 'order_prefix').upper()
        if not name or not order_prefix:
            return _fail('Name and order prefix are required.')
        brand = Brand.objects.create(
            name=name,
            order_prefix=order_prefix,
            logo=_clean(body, 'logo'),
            website=_clean(body, 'website'),
            instagram=_clean(body, 'instagram'),
        )
        return Response({'success': True, 'message': 'Brand added!', 'id': brand.id})

    def _edit(self, request, body):
        try:
            brand_id = int(body.get('id') or 0)
        except (TypeError, ValueError):
            brand_id = 0
        name = _clean(body, 'name')
        order_prefix = _clean(body, 'order_prefix').upper()
        if not brand_id or not name or not order_prefix:
            return _fail('Invalid parameters.')
        brand = Brand.objects.filter(id=brand_id).first()
        if not brand:
            return _fail('Error updating brand.')
        brand.name = name
        brand.order_prefix = order_prefix
        brand.logo = _clean(body, 'logo')
        brand.website = _clean(body, 'website')
        brand.instagram = _clean(body, 'instagram')
        brand.save(update_fields=['name', 'order_prefix', 'logo', 'website', 'instagram', 'updated_at'])
        return Response({'success': True, 'message': 'Brand updated!'})

    def _toggle(self, body):
        brand = self._get(body)
        if not brand:
            return _fail('Invalid ID.')
        brand.is_active = not brand.is_active
        brand.save(update_fields=['is_active', 'updated_at'])
        return Response({'success': True, 'message': 'Brand status toggled.'})

    def _delete(self, body):
        brand = self._get(body)
        if not brand:
            return _fail('Invalid ID.')
        in_use = (
            User.objects.filter(brand_id=brand.id).count()
            + Order.objects.filter(brand_id=brand.id).count()
        )
        if in_use:
            return _fail('Cannot delete: brand has existing users or orders. Deactivate instead.')
        brand.delete()
        return Response({'success': True, 'message': 'Brand deleted.'})

    @staticmethod
    def _get(body):
        try:
            brand_id = int(body.get('id') or 0)
        except (TypeError, ValueError):
            return None
        return Brand.objects.filter(id=brand_id).first() if brand_id else None
