"""
Storefront page views (public site), ported from the PHP pages:
index/shop/blog/blog-detail/policy/dashboard/print-invoice/logout.

Every page gets a CSRF cookie and the `is_loggedin` / `storefront_user`
context the shared navbar and dashboard rely on.
"""
import re

import markdown as markdown_lib
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import ensure_csrf_cookie

from accounts.models import PointsTransaction
from billing import services as billing
from billing.models import CreditNote
from core.models import Setting
from marketing.models import Blog
from orders.models import Order
from products.models import Flavor

from storefront import services

BLOG_PAGE_SIZE = 9
SITE_URL = 'https://varthaai.com'
_MD_STRIP_RE = re.compile(r'[#*`_~\[\]()]')
_H1_OPEN_RE = re.compile(r'<h1(\s[^>]*)?>', re.IGNORECASE)
_H1_CLOSE_RE = re.compile(r'</h1>', re.IGNORECASE)


def _gtm_container_id():
    return (
        Setting.objects.filter(setting_key='gtm_container_id')
        .values_list('setting_value', flat=True).first() or ''
    )


def _ctx(request, **extra):
    user = services.current_user(request)
    ctx = {
        'storefront_user': user,
        'is_loggedin': user is not None,
        'gtm_container_id': _gtm_container_id(),
    }
    ctx.update(extra)
    return ctx


def _published_blogs():
    return (
        Blog.objects.filter(is_published=True)
        .filter(Q(published_at__isnull=True) | Q(published_at__lte=timezone.now()))
        .select_related('created_by')
    )


def _blog_excerpt(blog_obj):
    if blog_obj.excerpt:
        return blog_obj.excerpt
    plain = _MD_STRIP_RE.sub('', blog_obj.content).replace('\n', ' ').strip()
    return (plain[:150] + '...') if len(plain) > 150 else plain


@ensure_csrf_cookie
def home(request):
    latest_blogs = list(_published_blogs().order_by('-published_at', '-created_at')[:3])
    for b in latest_blogs:
        b.list_excerpt = _blog_excerpt(b)
    return render(request, 'storefront/home.html', _ctx(request, latest_blogs=latest_blogs))


@ensure_csrf_cookie
def shop(request):
    flavors = list(Flavor.objects.filter(is_active=True).order_by('id'))
    for f in flavors:
        f.absolute_image_url = SITE_URL + f.image.url if f.image else ''
    return render(request, 'storefront/shop.html', _ctx(request, seo_flavors=flavors))


@ensure_csrf_cookie
def blog(request):
    qs = _published_blogs().order_by('-published_at', '-created_at')
    for b in qs:
        b.list_excerpt = _blog_excerpt(b)
    paginator = Paginator(qs, BLOG_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'storefront/blog.html', _ctx(request, page_obj=page_obj))


@ensure_csrf_cookie
def blog_detail(request):
    slug = request.GET.get('slug', '')
    blog_obj = _published_blogs().filter(slug=slug).first() if slug else None
    if blog_obj:
        html = markdown_lib.markdown(blog_obj.content, extensions=['extra', 'sane_lists'])
        # Demote in-content H1s so `.blog-title` remains the page's only H1.
        html = _H1_OPEN_RE.sub('<h2>', html)
        html = _H1_CLOSE_RE.sub('</h2>', html)
        blog_obj.content_html = mark_safe(html)
        blog_obj.seo_description = blog_obj.meta_description or _blog_excerpt(blog_obj)
        blog_obj.canonical_url = f'{SITE_URL}/blog/{blog_obj.slug}'
        blog_obj.absolute_image_url = (
            SITE_URL + blog_obj.featured_image.url if blog_obj.featured_image else ''
        )
    return render(request, 'storefront/blog-detail.html', _ctx(request, blog=blog_obj))


@ensure_csrf_cookie
def policy(request):
    return render(request, 'storefront/policy.html', _ctx(request))


@ensure_csrf_cookie
def dashboard(request):
    user = services.current_user(request)
    if not user:
        return redirect('/')
    return render(request, 'storefront/dashboard.html', _ctx(request))


def _customer_order(request, order_id):
    user = services.current_user(request)
    if not user or not order_id:
        return None
    return (
        Order.objects.filter(id=order_id, user=user)
        .select_related('user', 'referral', 'coupon')
        .prefetch_related('items', 'items__flavor').first()
    )


def _order_summary_ctx(request, order, for_pdf=False):
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
    total = subtotal - discount + order.delivery_charge
    loyalty = (
        PointsTransaction.objects.filter(user=order.user, status='credited')
        .aggregate(s=Sum('points'))['s'] or 0
    )
    return _ctx(
        request,
        order=order,
        items=items,
        subtotal=subtotal,
        total_quantity=total_quantity,
        discount=discount,
        total=total,
        loyalty_points=loyalty,
        referral_name=order.referral.name if order.referral else '',
        settings_map=dict(Setting.objects.values_list('setting_key', 'setting_value')),
        for_pdf=for_pdf,
        **billing.order_summary_context(
            order, lambda inv: f'/tax-invoice/{order.id}.pdf',
            lambda cn: f'/credit-note/{cn.id}.pdf', total,
        ),
    )


@ensure_csrf_cookie
def print_invoice(request):
    """Order Summary page (not a tax invoice). Kept at /print-invoice/ for old links."""
    if not services.current_user(request):
        return redirect('/')
    order = _customer_order(request, request.GET.get('id') or request.GET.get('order_id') or '')
    if not order:
        return redirect('/dashboard/')
    return render(request, 'storefront/print-invoice.html', _order_summary_ctx(request, order))


def _private_pdf(data_or_file, filename):
    if isinstance(data_or_file, (bytes, bytearray)):
        response = HttpResponse(data_or_file, content_type='application/pdf')
    else:
        response = FileResponse(data_or_file, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['X-Robots-Tag'] = 'noindex'
    response['Cache-Control'] = 'private, no-store'
    return response


def order_summary_pdf(request, order_id):
    """Order Summary as a PDF, rendered on demand."""
    from billing.pdf import render_html_pdf

    order = _customer_order(request, order_id)
    if not order:
        raise Http404
    data = render_html_pdf('storefront/print-invoice.html', _order_summary_ctx(request, order, for_pdf=True))
    return _private_pdf(data, f'order-summary-{order.id}.pdf')


def tax_invoice_pdf(request, order_id):
    """The stored Tax Invoice PDF — never regenerated once it exists."""
    order = _customer_order(request, order_id)
    invoice = billing.active_invoice(order) if order else None
    if not invoice:
        raise Http404
    if not invoice.pdf_file:
        invoice = billing.store_pdf(invoice.pk, 'invoice')
    return _private_pdf(invoice.pdf_file.open('rb'), invoice.number.replace('/', '-') + '.pdf')


def credit_note_pdf(request, pk):
    user = services.current_user(request)
    cn = CreditNote.objects.filter(pk=pk, invoice__b2c_order__user=user).first() if user else None
    if not cn:
        raise Http404
    if not cn.pdf_file:
        cn = billing.store_pdf(cn.pk, 'credit_note')
    return _private_pdf(cn.pdf_file.open('rb'), cn.number.replace('/', '-') + '.pdf')


def logout(request):
    services.logout_user(request)
    return redirect('/')


_ROBOTS_TXT = """User-agent: *
Allow: /

Sitemap: {site_url}/sitemap.xml
""".format(site_url=SITE_URL)


def robots_txt(request):
    return HttpResponse(_ROBOTS_TXT, content_type='text/plain')
