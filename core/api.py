"""
Shared DRF helpers and the module-permission contract.

Every admin API returns the envelope {success, message, data}. Use ok()/err()
so the shape stays consistent (the ported jQuery frontend depends on it).

Permission model (ported from the PHP session checks):
  - super_admin bypasses everything.
  - Otherwise permissions come from AdminUser.brand_permissions[<current brand id>].
  - "all" in that list is a wildcard.
  - A view names the module it guards via `permission_module`.

Public (storefront) endpoints do NOT use these — they set
`permission_classes = [AllowAny]` explicitly.
"""
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import exception_handler

# Session key holding the admin's active brand id.
BRAND_SESSION_KEY = 'brand_id'


def ok(data=None, message=''):
    return Response({'success': True, 'message': message, 'data': data})


def err(message, status=400, data=None):
    return Response({'success': False, 'message': message, 'data': data}, status=status)


def current_brand_id(request):
    """Active brand id for this session (may be None before one is chosen)."""
    return request.session.get(BRAND_SESSION_KEY)


def has_module_permission(user, brand_id, module):
    """True if `user` may access `module` within `brand_id`."""
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_super_admin', False):
        return True
    perms = (user.brand_permissions or {}).get(str(brand_id)) or []
    return 'all' in perms or module in perms


def api_exception_handler(exc, context):
    """
    Normalise DRF errors into the {success, message} envelope and return 401
    (not 403) for unauthenticated requests, so the ported utils.js redirects to
    the login page on an expired session. Permission-denied stays 403.
    """
    response = exception_handler(exc, context)
    if response is None:
        return response
    if isinstance(exc, (NotAuthenticated, AuthenticationFailed)):
        response.status_code = 401
        response.data = {
            'success': False,
            'message': 'Unauthorised. Please log in.',
            'redirect': '/admin/',
        }
    else:
        detail = response.data.get('detail') if isinstance(response.data, dict) else None
        message = str(detail) if detail else 'Request failed.'
        response.data = {'success': False, 'message': message}
    return response


class HasModulePermission(BasePermission):
    """
    DRF permission class. A view declares `permission_module = '<name>'`;
    if it omits it, only authentication is required.
    """

    message = 'You do not have permission for this action.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        module = getattr(view, 'permission_module', None)
        if not module:
            return True
        return has_module_permission(request.user, current_brand_id(request), module)
