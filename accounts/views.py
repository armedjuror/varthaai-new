import json

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from core.api import BRAND_SESSION_KEY
from core.auth import establish_admin_session


@ensure_csrf_cookie
def login_view(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')

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
        if establish_admin_session(request, user) is None:
            logout(request)
            return render(request, 'admin/login.html', {
                'error': 'Your account has no brand access.',
                'username': username,
            })
        return redirect('core:dashboard')

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
