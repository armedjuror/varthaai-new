"""
Public order link: /o/<token>/ — no login. Shows only what the bill needs
(name, items, amounts, delivery city/state, masked phone) with the PAID seal
or a Pay button, and serves the stored Tax Invoice / credit-note PDFs.

A wrong, revoked or expired token always gets the same plain 404.
"""
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from billing import links
from billing import services as billing
from billing.models import CreditNote
from billing.tax import ZERO, q2

RATE_LIMIT = 120          # requests …
RATE_WINDOW = 10 * 60     # … per IP per 10 minutes


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR', '')) or 'unknown'


def _private_headers(response):
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'no-referrer'
    response['Cache-Control'] = 'private, no-store'
    return response


def public_link(view):
    """Rate-limit by IP, resolve the token (404 if unusable), add headers."""
    @wraps(view)
    def wrapped(request, token, *args, **kwargs):
        key = f'order-link-rl:{_client_ip(request)}'
        count = cache.get_or_set(key, 0, RATE_WINDOW)
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, RATE_WINDOW)
            count = 1
        if count > RATE_LIMIT:
            return _private_headers(HttpResponse('Too many requests. Please try again later.', status=429))
        link = links.resolve(token)
        if link is None:
            return _private_headers(HttpResponse('Not found', status=404, content_type='text/plain'))
        try:
            response = view(request, link, *args, **kwargs)
        except Http404:
            response = HttpResponse('Not found', status=404, content_type='text/plain')
        return _private_headers(response)
    return wrapped


# ── page context ─────────────────────────────────────────────────────────

def _rows_and_totals(order):
    kind = links.kind_of(order)
    rows = []
    if kind == 'b2c':
        subtotal = ZERO
        for it in order.items.all().order_by('id'):
            total = q2(Decimal(str(it.sale_price_per_kg)) * it.quantity / 1000)
            subtotal += total
            rows.append({
                'name': it.flavor_name + (f' ({it.pack_label})' if it.pack_label else ''),
                'qty': f'{it.quantity:g} g', 'rate': f'₹{it.sale_price_per_kg:,.0f}/kg', 'total': total,
                'free': False,
            })
        discounts = []
        if order.coupon_discount:
            discounts.append((f'Discount ({order.coupon_code})' if order.coupon_code else 'Discount',
                              q2(order.coupon_discount)))
        delivery = q2(order.delivery_charge or 0)
        total = billing.b2c_order_total(order)
        paid = total if order.payment_status in ('paid', 'refund_initiated', 'refunded') else ZERO
    else:
        subtotal = order.subtotal
        for it in order.items.all().order_by('id'):
            rows.append({
                'name': it.flavor_name + (f' ({it.pack_label})' if it.pack_label else f' ({it.weight_grams}g)'),
                'qty': f'{it.quantity}', 'rate': '—' if it.is_free_item else f'₹{it.selling_price:,.2f}',
                'total': ZERO if it.is_free_item else q2(it.selling_price * it.quantity),
                'free': it.is_free_item, 'returned': it.returned_quantity,
            })
        discounts = []
        if order.discount_amount:
            discounts.append(('Discount', order.discount_amount))
        delivery = ZERO
        total = order.total_amount
        paid = order.paid_amount
    return rows, subtotal, discounts, delivery, total, paid


def _context(request, link, for_pdf=False):
    from core.models import Brand, Setting

    order = link.order
    token = link.token
    rows, subtotal, discounts, delivery, total, paid = _rows_and_totals(order)
    docs_ok = links.customer_docs_allowed(order)
    summary = billing.order_summary_context(
        order,
        lambda inv: f'/o/{token}/invoice.pdf' if docs_ok else None,
        lambda cn: f'/o/{token}/credit-note/{cn.id}.pdf' if docs_ok else None,
        total,
    )
    kind = links.kind_of(order)
    due = links.amount_due(order)
    return {
        'link': link, 'order': order, 'kind': kind,
        'brand': Brand.objects.filter(id=order.brand_id).first(),
        'settings_map': dict(Setting.objects.values_list('setting_key', 'setting_value')),
        'customer_name': order.name if kind == 'b2c' else order.company.company_name,
        'masked_mobile': links.mask_mobile(order.mobile if kind == 'b2c' else (
            order.contact.phone if order.contact_id and order.contact else '')),
        'delivery_area': links.delivery_area(order),
        'rows': rows, 'subtotal': subtotal, 'discounts': discounts, 'delivery': delivery,
        'total': total, 'paid': paid, 'due': due,
        'due_date': getattr(order, 'due_date', None),
        'can_pay': due > 0 and links.payments_enabled() and not for_pdf,
        'razorpay_key': settings.RAZORPAY_KEY_ID,
        'for_pdf': for_pdf,
        'docs_locked': not docs_ok,
        **summary,
    }


# ── views ────────────────────────────────────────────────────────────────

@ensure_csrf_cookie
@public_link
def order_page(request, link):
    links.record_view(link)
    return render(request, 'billing/share_page.html', _context(request, link))


@public_link
def summary_pdf(request, link):
    from billing.pdf import render_html_pdf

    data = render_html_pdf('billing/share_page.html', _context(request, link, for_pdf=True))
    response = HttpResponse(data, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="order-summary-{link.order_id}.pdf"'
    return response


@public_link
def invoice_pdf(request, link):
    invoice = billing.active_invoice(link.order)
    if invoice is None or not links.customer_docs_allowed(link.order):
        raise Http404
    if not invoice.pdf_file:
        invoice = billing.store_pdf(invoice.pk, 'invoice')
    response = FileResponse(invoice.pdf_file.open('rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{invoice.number.replace("/", "-")}.pdf"'
    return response


@public_link
def credit_note_pdf(request, link, pk):
    flt = {'invoice__b2c_order_id': link.b2c_order_id} if link.b2c_order_id else {
        'invoice__b2b_order_id': link.b2b_order_id}
    cn = CreditNote.objects.filter(pk=pk, **flt).first()
    if cn is None or not links.customer_docs_allowed(link.order):
        raise Http404
    if not cn.pdf_file:
        cn = billing.store_pdf(cn.pk, 'credit_note')
    response = FileResponse(cn.pdf_file.open('rb'), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{cn.number.replace("/", "-")}.pdf"'
    return response


@require_POST
@public_link
def pay_start(request, link):
    try:
        attempt = links.start_payment(link)
    except billing.BillingError as exc:
        return JsonResponse({'success': False, 'message': str(exc)})
    except Exception:  # noqa: BLE001 — gateway errors must not leak details
        return JsonResponse({'success': False, 'message': 'Payment gateway error. Please try again.'})
    from core.models import Brand

    brand = Brand.objects.filter(id=attempt.order.brand_id).values_list('name', flat=True).first() or ''
    return JsonResponse({
        'success': True, 'key_id': settings.RAZORPAY_KEY_ID, 'razorpay_order_id': attempt.razorpay_order_id,
        'amount': int(attempt.amount * 100), 'currency': 'INR', 'name': brand,
        'description': f'Order {attempt.order_id}',
    })


@require_POST
@public_link
def pay_verify(request, link):
    import json

    try:
        body = json.loads(request.body or '{}')
    except ValueError:
        body = {}
    rzp_order = body.get('razorpay_order_id') or ''
    payment_id = body.get('razorpay_payment_id') or ''
    signature = body.get('razorpay_signature') or ''
    flt = {'b2c_order_id': link.b2c_order_id} if link.b2c_order_id else {'b2b_order_id': link.b2b_order_id}
    from billing.models import PaymentAttempt

    if not PaymentAttempt.objects.filter(razorpay_order_id=rzp_order, **flt).exists():
        return JsonResponse({'success': False, 'message': 'Payment not found.'})
    if not (payment_id and signature and links.verify_signature(rzp_order, payment_id, signature)):
        return JsonResponse({'success': False, 'message': 'Payment verification failed.'})
    links.complete_payment(rzp_order, payment_id)
    return JsonResponse({'success': True, 'message': 'Payment received. Thank you!'})
