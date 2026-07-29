"""
Injects admin shell context (current admin, active brand, nav permissions,
brand switcher list) into every template. Consumed by templates/admin/base.html.
"""
from core.api import BRAND_SESSION_KEY

# Nav modules used to gate sidebar items (mirror of the PHP sidebar checks).
NAV_MODULES = [
    'dashboard', 'orders', 'coupons', 'flavors', 'customers', 'reviews',
    'b2b', 'stocks', 'expenses', 'blogs',
]


def admin_context(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {}

    from core.models import Brand

    is_super = getattr(user, 'is_super_admin', False)
    brand_id = request.session.get(BRAND_SESSION_KEY)

    if is_super:
        perms = set(NAV_MODULES)
        brand_qs = Brand.objects.filter(is_active=True).order_by('id')
    else:
        raw = (user.brand_permissions or {}).get(str(brand_id)) or []
        perms = set(NAV_MODULES) if 'all' in raw else set(raw)
        accessible_ids = [int(k) for k in (user.brand_permissions or {}).keys()]
        brand_qs = Brand.objects.filter(is_active=True, id__in=accessible_ids).order_by('id')

    current_brand = Brand.objects.filter(id=brand_id).first() if brand_id else None
    brands = list(brand_qs)

    return {
        'admin_user': user,
        'current_brand': current_brand,
        'nav_perms': perms,           # e.g. {'orders','b2b',...}; membership => show nav item
        'is_super_admin': is_super,
        'switchable_brands': brands if len(brands) > 1 else [],
    }
