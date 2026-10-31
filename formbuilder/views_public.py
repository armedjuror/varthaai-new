"""
Public form pages: /f/<public_id>/ (+ ?p=<token> for prefilled links).

No login. Responses carry noindex / no-store headers, POSTs are rate limited
per IP, and a hidden honeypot field catches naive bots.
"""
import math

from django.conf import settings
from django.core.cache import cache
from django.templatetags.static import static
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.utils.html import linebreaks
from django.utils.safestring import mark_safe
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from formbuilder import services, tokens
from formbuilder.models import Form, FormField

POST_LIMIT = 30           # submissions …
POST_WINDOW = 10 * 60     # … per IP per 10 minutes
HONEYPOT = 'website'
SESSION_KEY = 'form_done'


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR', '')) or 'unknown'


def _private(response):
    response['X-Robots-Tag'] = 'noindex, nofollow'
    response['Referrer-Policy'] = 'no-referrer'
    response['Cache-Control'] = 'private, no-store'
    return response


def _rate_limited(request):
    key = f'form-post-rl:{_client_ip(request)}'
    cache.add(key, 0, POST_WINDOW)
    try:
        return cache.incr(key) > POST_LIMIT
    except ValueError:
        cache.set(key, 1, POST_WINDOW)
        return False


def _theme(form):
    """Look and feel for the public pages. Varthaai's own forms (the storefront
    brand) get the mascot and grove; any other brand gets a neutral version
    with its own logo."""
    is_house = form.brand_id == getattr(settings, 'STOREFRONT_BRAND_ID', None)
    logo = ''
    if form.brand.logo:
        logo = form.brand.logo.url
    elif is_house:
        logo = static('images/logo.png')
    return {'is_house': is_house, 'brand_logo': logo}


def _rays(n=12):
    """Sunburst wedges (x1, y1, x2, y2) around (100, 100) for the thanks page."""
    def point(a):
        return round(100 + 140 * math.cos(a), 1), round(100 + 140 * math.sin(a), 1)

    out, half = [], math.pi / n / 2
    for i in range(n):
        a = 2 * math.pi * i / n
        out.append(point(a - half) + point(a + half))
    return out


def _get_form(public_id):
    form = Form.objects.select_related('key_field', 'brand').filter(public_id=public_id).first()
    if form is None or form.status == Form.DRAFT:
        raise Http404
    return form


def _render_form(request, form, prefill, values=None, errors=None, status=200):
    values, errors = values or {}, errors or {}
    items = []
    for f in form.fields.filter(is_archived=False):
        if f.slug in (prefill or {}):
            continue                      # prefilled by the link: never shown
        items.append({'field': f, 'value': values.get(f.slug, ''), 'error': errors.get(f.slug)})
    response = render(request, 'formbuilder/public_form.html', {
        'form': form, 'items': items, 'honeypot': HONEYPOT,
        'action': request.get_full_path(), 'form_error': errors.get('__all__'),
        'minutes': max(1, math.ceil(len(items) * 12 / 60)),   # ~12 s a question
        **_theme(form),
    }, status=status)
    return _private(response)


@csrf_protect
@require_http_methods(['GET', 'POST'])
def public_form(request, public_id):
    form = _get_form(public_id)
    if form.status == Form.CLOSED:
        return _private(render(request, 'formbuilder/closed.html', {'form': form, **_theme(form)}, status=410))

    prefill = services.resolve_prefill(form, request.GET.get(tokens.QUERY_PARAM))
    if request.method == 'GET':
        return _render_form(request, form, prefill)

    if _rate_limited(request):
        return _private(HttpResponse('Too many submissions. Please try again later.', status=429))
    if request.POST.get(HONEYPOT):
        return redirect('form_public:thanks', public_id=form.public_id)   # looks like success, stores nothing

    submission, errors = services.submit(form, request.POST, prefill)
    if errors:
        return _render_form(request, form, prefill, request.POST, errors, status=400)

    done = request.session.get(SESSION_KEY, {})
    done[form.public_id] = {'code': submission.code}
    request.session[SESSION_KEY] = done
    if form.success_redirect_url and not form.show_code:
        return redirect(form.success_redirect_url)
    return redirect('form_public:thanks', public_id=form.public_id)


def thanks(request, public_id):
    form = _get_form(public_id)
    done = (request.session.get(SESSION_KEY) or {}).get(form.public_id)
    if done is None:                       # opened directly, not after submitting
        return redirect('form_public:public', public_id=form.public_id)
    return _private(render(request, 'formbuilder/thanks.html', {
        'form': form,
        'message_html': mark_safe(linebreaks(form.success_message, autoescape=True)),  # escaped first
        'code': done.get('code', '') if form.show_code else '',
        'rays': _rays(),
        **_theme(form),
    }))
