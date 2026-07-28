"""
Storefront page views (public site), ported from the PHP pages:
index/shop/blog/blog-detail/policy/dashboard/print-invoice/logout.

Every page gets a CSRF cookie and the `is_loggedin` / `storefront_user`
context the shared navbar and dashboard rely on.
"""
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.models import PointsTransaction
from core.models import Setting
from orders.models import Order

from storefront import services


def _ctx(request, **extra):
    user = services.current_user(request)
    ctx = {'storefront_user': user, 'is_loggedin': user is not None}
    ctx.update(extra)
    return ctx


@ensure_csrf_cookie
def home(request):
    return render(request, 'storefront/home.html', _ctx(request))


@ensure_csrf_cookie
def shop(request):
    return render(request, 'storefront/shop.html', _ctx(request))


@ensure_csrf_cookie
def blog(request):
    return render(request, 'storefront/blog.html', _ctx(request))


@ensure_csrf_cookie
def blog_detail(request):
    return render(request, 'storefront/blog-detail.html',
                  _ctx(request, slug=request.GET.get('slug', '')))


@ensure_csrf_cookie
def policy(request):
    return render(request, 'storefront/policy.html', _ctx(request))


@ensure_csrf_cookie
def dashboard(request):
    user = services.current_user(request)
    if not user:
        return redirect('/')
    return render(request, 'storefront/dashboard.html', _ctx(request))


@ensure_csrf_cookie
def print_invoice(request):
    user = services.current_user(request)
    if not user:
        return redirect('/')
    order_id = request.GET.get('id') or request.GET.get('order_id') or ''
    order = (
        Order.objects.filter(id=order_id, user=user)
        .select_related('user', 'referral', 'coupon')
        .prefetch_related('items', 'items__flavor').first()
        if order_id else None
    )
    if not order:
        return redirect('/dashboard/')

    items = []
    subtotal = 0.0
    total_quantity = 0
    for it in order.items.all():
        item_total = it.quantity / 1000 * it.sale_price_per_kg
        subtotal += item_total
        total_quantity += it.quantity
        items.append({
            'name': it.flavor_name,
            'description': it.flavor.description if it.flavor else '',
            'quantity': it.quantity,
            'price_per_kg': it.price_per_kg,
            'sale_price_per_kg': it.sale_price_per_kg,
            'item_total': item_total,
        })
    discount = order.coupon_discount or 0.0
    loyalty = (
        PointsTransaction.objects.filter(user=order.user, status='credited')
        .aggregate(s=Sum('points'))['s'] or 0
    )
    return render(request, 'storefront/print-invoice.html', _ctx(
        request,
        order=order,
        items=items,
        subtotal=subtotal,
        total_quantity=total_quantity,
        discount=discount,
        total=subtotal - discount + order.delivery_charge,
        loyalty_points=loyalty,
        referral_name=order.referral.name if order.referral else '',
        settings_map=dict(Setting.objects.values_list('setting_key', 'setting_value')),
    ))


def logout(request):
    services.logout_user(request)
    return redirect('/')
