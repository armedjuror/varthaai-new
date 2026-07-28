"""
Settings slice (ported from PHP `admin/settings.php` + `admin/api/settings.php`).

Tabs: Business config, Loyalty points, Security (change own password),
Admin Users (super_admin only: CRUD + reset password), System info.

The API keeps the PHP response shape verbatim — GET returns
{success, settings, stats, admins?, brands?} (NOT the {success, message, data}
envelope) because the ported settings.js reads those keys directly. Business-
logic failures return HTTP 200 with {success:false, message} so the jQuery
`.done()` handler surfaces the message instead of the generic failure path.
"""
import platform

from django.db import IntegrityError
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AdminUser, User
from core.api import HasModulePermission
from core.auth import require_module
from core.models import Brand, Setting
from marketing.models import Review
from orders.models import Coupon, Order
from products.models import Flavor

VALID_ROLES = ('admin', 'staff', 'super_admin')


@require_module('settings')
@ensure_csrf_cookie
def settings_page(request):
    server_info = [
        ('Python Version', platform.python_version()),
        ('Server Software', 'Django'),
        ('Timezone', str(timezone.get_current_timezone())),
        ('Database', 'PostgreSQL'),
        ('App Version', '1.0.0'),
    ]
    return render(request, 'admin/settings.html', {'server_info': server_info})


def _fail(message):
    """PHP-parity failure: HTTP 200 so settings.js .done() shows the message."""
    return Response({'success': False, 'message': message})


def _normalise_brand_perms(role, raw):
    """super_admin or empty selection => NULL (full access), else the object."""
    if role == 'super_admin' or not raw:
        return None
    return raw


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


class SettingsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'settings'

    # -- GET -----------------------------------------------------------
    def get(self, request):
        settings_map = dict(
            Setting.objects.values_list('setting_key', 'setting_value')
        )
        stats = {
            'total_users': User.objects.count(),
            'total_orders': Order.objects.count(),
            'total_flavors': Flavor.objects.count(),
            'total_reviews': Review.objects.count(),
            'total_coupons': Coupon.objects.count(),
        }
        payload = {'success': True, 'settings': settings_map, 'stats': stats}

        if getattr(request.user, 'is_super_admin', False):
            payload['admins'] = [
                {
                    'id': a.id,
                    'username': a.username,
                    'name': a.name,
                    'role': a.role,
                    'brand_permissions': a.brand_permissions,
                    'last_login': a.last_login.isoformat() if a.last_login else None,
                    'created_at': a.created_at.isoformat() if a.created_at else None,
                }
                for a in AdminUser.objects.order_by('-created_at')
            ]
            payload['brands'] = list(
                Brand.objects.filter(is_active=True).order_by('id').values('id', 'name')
            )

        return Response(payload)

    # -- POST ----------------------------------------------------------
    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        is_super = getattr(request.user, 'is_super_admin', False)

        if action == 'update_settings':
            return self._update_settings(body)
        if action == 'change_password':
            return self._change_password(request, body)
        if action == 'create_admin':
            return self._create_admin(body) if is_super else _fail('Unauthorized.')
        if action == 'update_admin':
            return self._update_admin(body) if is_super else _fail('Unauthorized.')
        if action == 'reset_admin_password':
            return self._reset_admin_password(body) if is_super else _fail('Unauthorized.')
        if action == 'delete_admin':
            return self._delete_admin(request, body) if is_super else _fail('Unauthorized.')
        return _fail('Unknown action.')

    # -- action handlers -----------------------------------------------
    def _update_settings(self, body):
        data = body.get('data') or {}
        if not data:
            return _fail('No data provided.')
        try:
            for key, value in data.items():
                Setting.objects.update_or_create(
                    setting_key=str(key).strip(),
                    defaults={'setting_value': '' if value is None else str(value)},
                )
        except Exception:
            return _fail('Error saving settings.')
        return Response({'success': True, 'message': 'Settings saved!'})

    def _change_password(self, request, body):
        current = body.get('current_password') or ''
        new = body.get('new_password') or ''
        if len(new) < 6:
            return _fail('Password must be at least 6 characters.')
        if not request.user.check_password(current):
            return _fail('Current password is incorrect.')
        request.user.set_password(new)
        request.user.save(update_fields=['password'])
        return Response({'success': True, 'message': 'Password changed successfully!'})

    def _create_admin(self, body):
        username = (body.get('username') or '').strip()
        name = (body.get('name') or '').strip()
        role = body.get('role') if body.get('role') in VALID_ROLES else 'admin'
        password = body.get('password') or ''
        brand_perms = _normalise_brand_perms(role, body.get('brand_permissions'))
        if not username or not name or len(password) < 6:
            return _fail('Username, name and password (min 6 chars) are required.')
        try:
            AdminUser.objects.create_user(
                username=username, password=password,
                name=name, role=role, brand_permissions=brand_perms,
            )
        except IntegrityError:
            return _fail('Error creating admin. Username may already exist.')
        return Response({'success': True, 'message': 'Admin user created!'})

    def _update_admin(self, body):
        admin_id = _to_int(body.get('id'))
        name = (body.get('name') or '').strip()
        role = body.get('role') if body.get('role') in VALID_ROLES else 'admin'
        brand_perms = _normalise_brand_perms(role, body.get('brand_permissions'))
        if not admin_id or not name:
            return _fail('Invalid parameters.')
        admin = AdminUser.objects.filter(id=admin_id).first()
        if not admin:
            return _fail('Error updating admin user.')
        admin.name = name
        admin.role = role
        admin.brand_permissions = brand_perms
        admin.save(update_fields=['name', 'role', 'brand_permissions'])
        return Response({'success': True, 'message': 'Admin user updated!'})

    def _reset_admin_password(self, body):
        admin_id = _to_int(body.get('id'))
        new = body.get('new_password') or ''
        if not admin_id or len(new) < 6:
            return _fail('Valid admin ID and password (min 6 chars) required.')
        admin = AdminUser.objects.filter(id=admin_id).first()
        if not admin:
            return _fail('Error resetting password.')
        admin.set_password(new)
        admin.save(update_fields=['password'])
        return Response({'success': True, 'message': 'Password reset successfully!'})

    def _delete_admin(self, request, body):
        admin_id = _to_int(body.get('id'))
        if not admin_id or admin_id == request.user.id:
            return _fail('Cannot delete your own account.')
        deleted, _ = AdminUser.objects.filter(id=admin_id).delete()
        if not deleted:
            return _fail('Error deleting admin user.')
        return Response({'success': True, 'message': 'Admin user deleted.'})
