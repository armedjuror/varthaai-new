import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from core.api import BRAND_SESSION_KEY, current_brand_id, has_module_permission
from core.auth import establish_admin_session


def _post_login_redirect_target(user, brand_id):
    """
    Where to land after login. Most accounts have 'dashboard' permission and
    go there; an account whose ONLY module is 'field_employee' (a pure field
    employee — see sessions_tracking.permissions) has no dashboard access and
    goes straight to their own session-tracking page instead. Field-employee
    is just a permission, not a role, so an account can have both and still
    lands on the dashboard — "My Day" is one click away in the sidebar.
    """
    if has_module_permission(user, brand_id, 'dashboard'):
        return 'core:dashboard'
    if has_module_permission(user, brand_id, 'field_employee'):
        return 'sessions_tracking:my_day'
    return 'core:dashboard'


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect_target(request.user, current_brand_id(request)))

    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)
        if user is None:
            return render(request, 'admin/login.html', {
                'error': 'Invalid username or password',
                'username': username,
            })
        login(request, user)
        brand_id = establish_admin_session(request, user)
        if brand_id is None:
            logout(request)
            return render(request, 'admin/login.html', {
                'error': 'Your account has no brand access.',
                'username': username,
            })
        return redirect(_post_login_redirect_target(user, brand_id))

    return render(request, 'admin/login.html')


def logout_view(request):
    logout(request)
    return redirect('accounts:login')


@login_required
@require_POST
def brand_switch(request):
    try:
        body = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'message': 'Invalid request body.'})

    brand_id = body.get('brand_id')
    try:
        brand_id = int(brand_id)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'message': 'Invalid brand.'})

    user = request.user
    if user.is_super_admin:
        from core.models import Brand
        allowed = Brand.objects.filter(id=brand_id, is_active=True).exists()
    else:
        allowed = str(brand_id) in (user.brand_permissions or {})

    if not allowed:
        return JsonResponse({'success': False, 'message': 'You do not have access to this brand.'})

    request.session[BRAND_SESSION_KEY] = brand_id
    return JsonResponse({'success': True, 'message': 'Brand switched'})
