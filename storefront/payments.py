"""
Storefront payment gateways: Razorpay or Cashfree.

Which one checkout uses is a runtime switch: the `payment_gateway` setting
(Admin → Settings → Payments), falling back to settings.PAYMENT_GATEWAY. If
the chosen gateway has no API keys configured, checkout falls back to the
other one so the shop never goes down because of a half-finished switch.

Each order remembers its gateway (Order.payment_gateway), so verifying,
webhooks and refunds always go to the gateway that took the payment, even
after the admin switches.

Cashfree (PG API, https://www.cashfree.com/docs/api-reference/payments/latest/overview):
  - our order id is used as the Cashfree order_id (alphanumeric, _ and -)
  - the browser opens checkout with the returned payment_session_id
  - payment is confirmed server-side (GET /orders/{id}) or by the signed webhook
"""
import base64
import hashlib
import hmac
import logging

import requests
from django.conf import settings

log = logging.getLogger(__name__)

RAZORPAY = 'razorpay'
CASHFREE = 'cashfree'
GATEWAYS = (RAZORPAY, CASHFREE)
GATEWAY_LABELS = {RAZORPAY: 'Razorpay', CASHFREE: 'Cashfree'}
SETTING_KEY = 'payment_gateway'

CASHFREE_TIMEOUT = 20  # seconds


class CashfreeError(Exception):
    pass


# ── gateway selection ────────────────────────────────────────────────────

def is_configured(gateway):
    if gateway == RAZORPAY:
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)
    if gateway == CASHFREE:
        return bool(settings.CASHFREE_APP_ID and settings.CASHFREE_SECRET_KEY)
    return False


def selected_gateway():
    """What the admin chose (or the .env default)."""
    from core.models import Setting

    value = (Setting.objects.filter(setting_key=SETTING_KEY)
             .values_list('setting_value', flat=True).first() or '').strip().lower()
    if value in GATEWAYS:
        return value
    default = (settings.PAYMENT_GATEWAY or '').strip().lower()
    return default if default in GATEWAYS else RAZORPAY


def active_gateway():
    """The gateway new checkouts use: the selected one, unless it has no keys
    and the other one does."""
    chosen = selected_gateway()
    if is_configured(chosen):
        return chosen
    for other in GATEWAYS:
        if other != chosen and is_configured(other):
            log.warning('Payment gateway %s has no API keys; falling back to %s', chosen, other)
            return other
    return chosen


def status_payload():
    """For the admin Payments tab."""
    return {
        'selected': selected_gateway(),
        'active': active_gateway(),
        'gateways': [{
            'key': g, 'label': GATEWAY_LABELS[g], 'configured': is_configured(g),
        } for g in GATEWAYS],
        'cashfree_env': cashfree_mode(),
        'webhooks': {
            RAZORPAY: f'{settings.SITE_URL.rstrip("/")}/api/razorpay-webhook/',
            CASHFREE: f'{settings.SITE_URL.rstrip("/")}/api/cashfree-webhook/',
        },
    }


# ── Cashfree API ─────────────────────────────────────────────────────────

def cashfree_mode():
    """'production' or 'sandbox' — also the JS SDK's `mode`."""
    return 'production' if settings.CASHFREE_ENV in ('production', 'prod', 'live') else 'sandbox'


def _cashfree_base():
    if cashfree_mode() == 'production':
        return 'https://api.cashfree.com/pg'
    return 'https://sandbox.cashfree.com/pg'


def _cashfree(method, path, payload=None, idempotency_key=None):
    headers = {
        'accept': 'application/json',
        'content-type': 'application/json',
        'x-api-version': settings.CASHFREE_API_VERSION,
        'x-client-id': settings.CASHFREE_APP_ID,
        'x-client-secret': settings.CASHFREE_SECRET_KEY,
    }
    if idempotency_key:
        headers['x-idempotency-key'] = idempotency_key
    try:
        res = requests.request(method, _cashfree_base() + path, json=payload,
                               headers=headers, timeout=CASHFREE_TIMEOUT)
    except requests.RequestException as exc:
        raise CashfreeError(f'Could not reach Cashfree: {exc}') from exc
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code >= 400:
        message = (data.get('message') if isinstance(data, dict) else None) or res.text[:200]
        raise CashfreeError(message or f'Cashfree error {res.status_code}')
    return data


def cashfree_create_order(order_id, amount, *, customer_id, phone, name='', note='',
                          return_url='', notify_url=''):
    customer = {'customer_id': customer_id, 'customer_phone': phone}
    if len(name) >= 3:  # Cashfree: 3–100 chars, optional
        customer['customer_name'] = name[:100]
    meta = {}
    if return_url:
        meta['return_url'] = return_url
    if notify_url.startswith('https://'):  # Cashfree only accepts an https notify_url
        meta['notify_url'] = notify_url
    payload = {
        'order_id': order_id,
        'order_amount': round(float(amount), 2),
        'order_currency': 'INR',
        'customer_details': customer,
    }
    if meta:
        payload['order_meta'] = meta
    if note:
        payload['order_note'] = note[:200]
    data = _cashfree('POST', '/orders', payload)
    if not data.get('payment_session_id'):
        raise CashfreeError('Cashfree did not return a payment session.')
    return data


def cashfree_get_order(order_id):
    return _cashfree('GET', f'/orders/{order_id}')


def cashfree_successful_payment(order_id):
    """The order's SUCCESS payment, or None."""
    payments = _cashfree('GET', f'/orders/{order_id}/payments')
    for p in payments if isinstance(payments, list) else []:
        if p.get('payment_status') == 'SUCCESS':
            return p
    return None


def cashfree_refund(order_id, amount, refund_id, note=''):
    payload = {'refund_amount': round(float(amount), 2), 'refund_id': refund_id}
    if note:
        payload['refund_note'] = note[:100]
    return _cashfree('POST', f'/orders/{order_id}/refunds', payload, idempotency_key=refund_id)


def cashfree_verify_webhook(raw_body, timestamp, signature):
    """base64(HMAC-SHA256(timestamp + raw_body, secret_key)) == signature."""
    secret = settings.CASHFREE_SECRET_KEY or ''
    if not (secret and timestamp and signature):
        return False
    message = timestamp.encode() + raw_body
    expected = base64.b64encode(hmac.new(secret.encode(), message, hashlib.sha256).digest()).decode()
    return hmac.compare_digest(expected, signature)


# Refund statuses Cashfree reports, mapped onto billing.Refund.Status.
CASHFREE_REFUND_DONE = ('SUCCESS',)
CASHFREE_REFUND_FAILED = ('CANCELLED', 'REJECTED')
