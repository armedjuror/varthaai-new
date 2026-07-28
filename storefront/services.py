"""
Storefront service helpers (ported from PHP `config.php` + `helpers/sms_helper.php`).

External integrations degrade gracefully when their secret is unset:
  - reCAPTCHA: blank RECAPTCHA_SECRET => verification is skipped (dev).
  - 2Factor OTP: blank TWOFACTOR_API_KEY => send/verify return a config error.
  - Telegram: blank TELEGRAM_BOT_TOKEN => notifications are silently skipped.

The storefront customer session is separate from the admin session:
`request.session['storefront_user_id']` / `['storefront_user_name']`.
"""
import logging
import re
from uuid import uuid4

import requests
from django.conf import settings

from accounts.models import PointsTransaction, User

log = logging.getLogger(__name__)

SESSION_USER_ID = 'storefront_user_id'
SESSION_USER_NAME = 'storefront_user_name'

_HTTP_TIMEOUT = 10


# ── storefront session ────────────────────────────────────────────────
def login_user(request, user):
    request.session[SESSION_USER_ID] = user.id
    request.session[SESSION_USER_NAME] = user.name or ''


def logout_user(request):
    request.session.pop(SESSION_USER_ID, None)
    request.session.pop(SESSION_USER_NAME, None)


def current_user(request):
    uid = request.session.get(SESSION_USER_ID)
    if not uid:
        return None
    return User.objects.filter(id=uid).first()


# ── brand ─────────────────────────────────────────────────────────────
def storefront_brand_id():
    return settings.STOREFRONT_BRAND_ID


# ── order id (mirrors orders.views_b2c: "<prefix>_<13 hex>") ───────────
def generate_order_id(brand):
    prefix = (getattr(brand, 'order_prefix', None) or 'ORD')
    return f'{prefix}_' + uuid4().hex[:13]


# ── mobile validation (port of SMSHelper::validateAndFormatMobile) ────
def validate_and_format_mobile(mobile):
    mobile = re.sub(r'[^0-9]', '', mobile or '')
    mobile = re.sub(r'^91', '', mobile)
    if len(mobile) != 10:
        return {'success': False, 'message': 'Please enter a valid 10-digit mobile number'}
    if not re.match(r'^[6-9]', mobile):
        return {'success': False, 'message': 'Mobile number should start with 6, 7, 8, or 9'}
    return {'success': True, 'mobile': '+91' + mobile}


def clean_mobile(mobile):
    """Bare 10-digit form stored in the DB (drops the +91)."""
    v = validate_and_format_mobile(mobile)
    return v['mobile'].replace('+91', '') if v['success'] else None


# ── reCAPTCHA (port of verifyRecaptcha) ───────────────────────────────
def verify_recaptcha(token):
    if not settings.RECAPTCHA_SECRET:
        return True  # not configured => skip (dev convenience)
    if not token:
        return False
    try:
        resp = requests.post(
            'https://www.google.com/recaptcha/api/siteverify',
            data={'secret': settings.RECAPTCHA_SECRET, 'response': token},
            timeout=_HTTP_TIMEOUT,
        )
        return resp.status_code == 200 and resp.json().get('success') is True
    except requests.RequestException:
        return False


# ── 2Factor OTP (port of SMSHelper::sendOTP / verifyOTP) ───────────────
def send_otp(mobile):
    validation = validate_and_format_mobile(mobile)
    if not validation['success']:
        return validation
    if not settings.TWOFACTOR_API_KEY:
        return {'success': False, 'message': 'OTP service is not configured.'}
    formatted = validation['mobile']
    url = (
        f'https://2factor.in/API/V1/{settings.TWOFACTOR_API_KEY}'
        f'/SMS/{formatted}/AUTOGEN2/{settings.OTP_TEMPLATE}'
    )
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException:
        return {'success': False, 'message': 'Oops, Something went wrong!. Please try again.'}
    if resp.status_code != 200:
        return {'success': False, 'message': 'Oops, Something went wrong!. Please try again.'}
    data = resp.json() if resp.content else {}
    if data.get('Status') == 'Success':
        return {'success': True, 'message': f'OTP sent successfully to {formatted}'}
    return {'success': False, 'message': 'Failed to send OTP. Please try again.'}


def verify_otp(mobile, otp):
    validation = validate_and_format_mobile(mobile)
    if not validation['success']:
        return validation
    if len(otp or '') != 6 or not str(otp).isdigit():
        return {'success': False, 'message': 'Please enter a valid 6-digit OTP'}
    if not settings.TWOFACTOR_API_KEY:
        return {'success': False, 'message': 'OTP service is not configured.'}
    formatted = validation['mobile']
    url = f'https://2factor.in/API/V1/{settings.TWOFACTOR_API_KEY}/SMS/VERIFY3/{formatted}/{otp}'
    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException:
        return {'success': False, 'message': 'Network error. Please try again.'}
    if resp.status_code != 200:
        return {'success': False, 'message': 'Network error. Please try again.'}
    data = resp.json() if resp.content else {}
    if data.get('Status') == 'Success' and data.get('Details') == 'OTP Matched':
        return {'success': True, 'message': 'OTP verified successfully'}
    return {'success': False, 'message': 'Invalid OTP or OTP expired'}


# ── loyalty points (port of calculatePurchasePoints / addPointsToUser) ─
def calculate_purchase_points(order_value):
    return round(order_value * settings.PURCHASE_POINTS_PERCENTAGE / 100)


def add_points(user_id, points, type_, reference):
    """
    Create a pending points transaction. Uniqueness is per (user, reference),
    so the buyer's purchase row and the referrer's referral row can share an
    order id, but crediting the SAME user twice for one reference is swallowed
    (idempotent) rather than raising.
    """
    from django.db import IntegrityError, transaction

    try:
        with transaction.atomic():
            PointsTransaction.objects.create(
                user_id=user_id, points=points, type=type_,
                reference=str(reference), status='pending',
            )
        return True
    except IntegrityError:
        log.warning('points_transactions.reference collision for %s (%s)', reference, type_)
        return False


# ── Telegram new-order notification (port of notifyNewOrder) ───────────
def notify_new_order(order, items):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_GROUP_CHAT_ID:
        return
    lines = [
        '<b>🔔 New Order Received!</b>\n',
        f'<b>Order ID:</b> {order.id}',
        f'<b>Customer:</b> {order.name}',
        f'<b>Mobile:</b> {order.mobile}',
        f'<b>Status:</b> {order.status}',
        f'<b>Payment Status:</b> {order.payment_status}',
        f'<b>Delivery Address:</b> \n{order.address}',
        f'<b>Pincode:</b> \n{order.pincode}',
        '<b>Items:</b>',
    ]
    for i, it in enumerate(items, 1):
        lines.append(f'{i}. Flavour: {it.flavor_name}, Quantity: {it.quantity}')
    message = '\n'.join(lines)
    try:
        requests.post(
            f'https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': settings.TELEGRAM_GROUP_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=_HTTP_TIMEOUT,
        )
    except requests.RequestException:
        log.warning('Telegram notification failed for order %s', order.id)
