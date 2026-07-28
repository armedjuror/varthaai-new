"""
Page-level (non-API) auth helpers for admin HTML views.

APIs use core.api.HasModulePermission; regular Django views use these
decorators, which redirect to the login page instead of returning JSON.
"""
from functools import wraps

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from core.api import BRAND_SESSION_KEY, has_module_permission

User = get_user_model()


def _accessible_brand_ids(user):
    """Brand ids this admin may see (empty list => all, for super_admin)."""
    if getattr(user, 'is_super_admin', False):
        return []
    return [int(k) for k in (user.brand_permissions or {}).keys()]


def establish_admin_session(request, user):
    """
    Call right after django.contrib.auth.login(). Seeds the active brand so
    permission checks and the brand switcher work. Returns the chosen brand id,
    or None if a non-super_admin has no brand access (caller should reject).
    """
    from core.models import Brand

    accessible = _accessible_brand_ids(user)
    if user.is_super_admin:
        brand = Brand.objects.filter(is_active=True).order_by('id').first()
    else:
        if not accessible:
            return None
        brand = Brand.objects.filter(is_active=True, id__in=accessible).order_by('id').first()
    if brand is None:
        return None
    request.session[BRAND_SESSION_KEY] = brand.id
    return brand.id


def admin_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not (request.user and request.user.is_authenticated):
            return redirect('accounts:login')
        return view_func(request, *args, **kwargs)
    return _wrapped


def require_module(module):
    """Guard an admin HTML view by module permission for the active brand."""
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not (request.user and request.user.is_authenticated):
                return redirect('accounts:login')
            brand_id = request.session.get(BRAND_SESSION_KEY)
            if not has_module_permission(request.user, brand_id, module):
                raise PermissionDenied(f'No permission for {module}.')
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
