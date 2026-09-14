"""
Brand Assets admin page — the logo/product/lifestyle/team/event photo
library poster-generation-plan.md §3 calls "the highest-leverage piece,
since it's what makes new posters look like *your* posters" once composited
in by content.agents.designer.generate_poster_image. Existed as a bare
model (content.models.BrandAsset) with no admin surface at all until now —
Designer's generation calls never selected/passed one either (see
designer.py's select_brand_asset, added alongside this page).

Reached from the Brands table, same as Brand Kit (core/brands_views.py) —
super_admin only, no separate permission_module, per content-generator-
plan.md §14.2's precedent ("Brand Kit needs no new key ... inherits
[Brands'] gate, consistently with Brand management already being
super-admin-only"). Lives in the `content` app (not `core`) because
BrandAsset is a content-app model — core doesn't import from content
anywhere else, and shouldn't start here just to keep this one page's file
location tidy; the Brands page links to it by URL, same as it does for
Brand Kit's own separate page.
"""
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView

from core.api import err, ok
from core.auth import admin_login_required
from core.models import Brand

from content.models import BrandAsset


def _super_admin_only(request):
    return getattr(request.user, 'is_super_admin', False)


@admin_login_required
@ensure_csrf_cookie
def brand_assets_page(request, pk):
    if not _super_admin_only(request):
        return redirect('core:dashboard')
    brand = Brand.objects.filter(id=pk).first()
    if brand is None:
        return redirect('core:brands')
    return render(request, 'admin/brand-assets.html', {'brand': brand})


def _asset_dict(a):
    return {
        'id': a.id,
        'image_url': a.image.url if a.image else None,
        'tag': a.tag,
        'label': a.label,
        'is_active': a.is_active,
        'created_at': a.created_at.isoformat(),
    }


class BrandAssetsAPI(APIView):
    """GET list / POST add|toggle|delete, all super_admin-only (same gate as
    every other view in this module — no HasModulePermission/permission_module,
    matching brands_views.py's own BrandKitAPI/BrandsAPI convention)."""
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, pk):
        get_object_or_404(Brand, id=pk)
        assets = BrandAsset.objects.filter(brand_id=pk).order_by('-created_at')
        return ok({'items': [_asset_dict(a) for a in assets]})

    def post(self, request, pk):
        if not _super_admin_only(request):
            return err('Super admin only.')
        brand = Brand.objects.filter(id=pk).first()
        if brand is None:
            return err('Brand not found.', status=404)

        action = request.data.get('action')
        if action == 'add':
            return self._add(request, brand)
        if action == 'toggle':
            return self._toggle(request, brand)
        if action == 'delete':
            return self._delete(request, brand)
        return err('Unknown action.')

    def _add(self, request, brand):
        image = request.FILES.get('image')
        if not image:
            return err('An image file is required.')
        tag = request.data.get('tag') or BrandAsset.Tag.OTHER
        if tag not in BrandAsset.Tag.values:
            return err(f'tag must be one of {list(BrandAsset.Tag.values)}.')
        asset = BrandAsset.objects.create(
            brand=brand, image=image, tag=tag, label=(request.data.get('label') or '').strip(),
        )
        return ok(_asset_dict(asset), message='Asset added.')

    def _get_asset(self, request, brand):
        try:
            asset_id = int(request.data.get('id') or 0)
        except (TypeError, ValueError):
            return None
        return BrandAsset.objects.filter(id=asset_id, brand=brand).first()

    def _toggle(self, request, brand):
        asset = self._get_asset(request, brand)
        if not asset:
            return err('Asset not found.', status=404)
        asset.is_active = not asset.is_active
        asset.save(update_fields=['is_active'])
        return ok({'is_active': asset.is_active})

    def _delete(self, request, brand):
        asset = self._get_asset(request, brand)
        if not asset:
            return err('Asset not found.', status=404)
        asset.image.delete(save=False)
        asset.delete()
        return ok(message='Asset deleted.')
