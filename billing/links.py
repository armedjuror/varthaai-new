"""
Order share links, paying from the link, and one-click WhatsApp messages
(GST invoicing plan, section 10 / Phase 2A).

  - ShareLink: an open link with a long secret token that shows the Order
    Summary, a PAID seal or a Pay button, and the Tax Invoice / credit-note
    PDFs. One live link per order; regenerating revokes the old one.
  - PaymentAttempt: a Razorpay order created for the current balance. The
    browser return call and the webhook both settle it; both are idempotent
    on the Razorpay payment id.
  - MessageLog: every WhatsApp button click ("Order details sent" /
    "Payment reminder sent"). WhatsApp only opens with the text ready — the
    sender still taps Send, so the log records what was prepared.
"""
import logging
import re
import secrets
import urllib.parse
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from billing import services as billing
from billing.gst_states import state_name
from billing.models import CreditNote, MessageLog, PaymentAttempt, ShareLink
from billing.tax import ZERO, q2

log = logging.getLogger(__name__)

LINK_VALID_DAYS_AFTER_PAID = 60

DEFAULT_MESSAGES = {
    'wa_message_delivery': (
        'Hi {name}, your {brand} order {order_id} has been delivered. '
        'Order summary and tax invoice: {link}'
    ),
    'wa_message_reminder': (
        'Hi {name}, a gentle reminder: ₹{amount} is due on {due_date} for order {order_id}. '
        'You can view the bill and pay here: {link}'
    ),
}


# ── order helpers ────────────────────────────────────────────────────────

def kind_of(order):
    return billing._order_kind(order)


def _ref(order):
    return {'b2c_order': order} if kind_of(order) == 'b2c' else {'b2b_order': order}


def amount_due(order):
    """What the Pay button asks for: never more than is owed, and credit
    notes reduce it first."""
    if kind_of(order) == 'b2b':
        return max(ZERO, q2(order.total_amount - order.paid_amount))
    if order.payment_status not in ('pending', 'failed'):
        return ZERO
    if order.status in ('cancelled', 'deleted', 'failed'):
        return ZERO
    credited = sum((cn.grand_total for cn in CreditNote.objects.filter(
        invoice__b2c_order=order, invoice__status='active')), ZERO)
    return max(ZERO, q2(billing.b2c_order_total(order) - credited))


def paid_on(order):
    """Date the order became fully paid, or None."""
    if kind_of(order) == 'b2c':
        if order.payment_status != 'paid':
            return None
        return billing.ist_date(order.payment_date or order.updated_at)
    if order.total_amount <= 0 or order.paid_amount < order.total_amount:
        return None
    return (order.payments.filter(payment_type='payment').order_by('-payment_date')
            .values_list('payment_date', flat=True).first()) or billing.ist_date(order.updated_at)


# ── share links ──────────────────────────────────────────────────────────

def live_link(order):
    return ShareLink.objects.filter(revoked_at__isnull=True, **_ref(order)).order_by('-id').first()


def get_or_create_link(order, user=None):
    with transaction.atomic():
        type(order).objects.select_for_update().filter(pk=order.pk).first()
        link = live_link(order)
        if link:
            return link
        return ShareLink.objects.create(
            token=secrets.token_urlsafe(32), created_by=user if getattr(user, 'pk', None) else None, **_ref(order),
        )


def regenerate_link(order, user=None):
    with transaction.atomic():
        ShareLink.objects.filter(revoked_at__isnull=True, **_ref(order)).update(revoked_at=timezone.now())
        return ShareLink.objects.create(
            token=secrets.token_urlsafe(32), created_by=user if getattr(user, 'pk', None) else None, **_ref(order),
        )


def link_expires_on(link):
    """60 days after the order is paid; never while something is due."""
    paid = paid_on(link.order)
    return paid + timedelta(days=LINK_VALID_DAYS_AFTER_PAID) if paid else None


def resolve(token):
    """The live, unexpired link for `token`, or None (callers answer 404)."""
    if not token or len(token) > 64:
        return None
    link = (ShareLink.objects.filter(token=token, revoked_at__isnull=True)
            .select_related('b2c_order', 'b2b_order').first())
    if link is None or link.order is None:
        return None
    expires = link_expires_on(link)
    if expires and billing.ist_today() > expires:
        return None
    return link


def record_view(link):
    from django.db.models import F

    ShareLink.objects.filter(pk=link.pk).update(view_count=F('view_count') + 1, last_viewed_at=timezone.now())


def link_url(link, request=None):
    path = f'/o/{link.token}/'
    if request is not None:
        return request.build_absolute_uri(path)
    return getattr(settings, 'SITE_URL', 'https://varthaai.com').rstrip('/') + path


def mask_mobile(mobile):
    digits = re.sub(r'\D', '', mobile or '')
    return ('•' * max(0, len(digits) - 4) + digits[-4:]) if digits else ''


# ── paying from the link ─────────────────────────────────────────────────

def _razorpay_client():
    import razorpay

    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def payments_enabled():
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def start_payment(link):
    """Create a Razorpay order for the current balance."""
    if not payments_enabled():
        raise billing.BillingError('Online payment is not available right now.')
    order = link.order
    with transaction.atomic():
        order = type(order).objects.select_for_update().get(pk=order.pk)
        due = amount_due(order)
        if due <= 0:
            raise billing.BillingError('Nothing is due on this order.')
        rzp = _razorpay_client().order.create({
            'amount': int(due * 100), 'currency': 'INR', 'receipt': str(order.pk)[:40],
            'notes': {'order_id': str(order.pk), 'source': 'share_link'},
        })
        attempt = PaymentAttempt.objects.create(
            share_link=link, razorpay_order_id=rzp['id'], amount=due, **_ref(order),
        )
    return attempt


def verify_signature(razorpay_order_id, payment_id, signature):
    import razorpay

    try:
        _razorpay_client().utility.verify_payment_signature({
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature,
        })
        return True
    except razorpay.errors.SignatureVerificationError:
        return False


def complete_payment(razorpay_order_id, payment_id):
    """Record a captured link payment once. Safe to call from both the
    browser return and the webhook, in any order, any number of times.
    Returns the attempt, or None when the Razorpay order is not a link
    payment."""
    from finance.services import sync_b2b_order_income, sync_b2c_order_income

    with transaction.atomic():
        attempt = PaymentAttempt.objects.select_for_update().filter(razorpay_order_id=razorpay_order_id).first()
        if attempt is None:
            return None
        if attempt.status == PaymentAttempt.Status.PAID:
            return attempt
        if payment_id and PaymentAttempt.objects.filter(razorpay_payment_id=payment_id).exists():
            return attempt
        order = type(attempt.order).objects.select_for_update().get(pk=attempt.order_id)
        attempt.status = PaymentAttempt.Status.PAID
        attempt.razorpay_payment_id = payment_id or None
        attempt.paid_at = timezone.now()
        attempt.save(update_fields=['status', 'razorpay_payment_id', 'paid_at'])
        if kind_of(order) == 'b2c':
            from orders.views_b2c import _sync_loyalty

            order.payment_status = 'paid'
            order.payment_date = timezone.now()
            order.razorpay_payment_id = payment_id or ''
            fields = ['payment_status', 'payment_date', 'razorpay_payment_id', 'updated_at']
            if order.status in ('pending', 'failed', 'cancelled'):
                order.status = 'confirmed'
                fields.append('status')
            order.save(update_fields=fields)
            _sync_loyalty(order.id, 'paid')
            sync_b2c_order_income(order)
        else:
            apply_b2b_payment(order, attempt.amount, method='online', reference=payment_id or '',
                              notes='Paid online from the order link', user=None)
            sync_b2b_order_income(order)
        billing._activity(None, f'Online payment ₹{attempt.amount} received', f'Order {order.pk} ({payment_id})',
                          module='orders')
    return attempt


def fail_payment(razorpay_order_id):
    PaymentAttempt.objects.filter(
        razorpay_order_id=razorpay_order_id, status=PaymentAttempt.Status.CREATED,
    ).update(status=PaymentAttempt.Status.FAILED)


def apply_b2b_payment(order, amount, *, method, reference='', notes='', payment_date=None,
                      user=None, payment_type='payment'):
    """Record a B2B payment / refund against `order` and update its paid
    amount and status (the single place both the admin form and online
    payments go through)."""
    from crm.models import B2BActivity
    from finance.services import sync_b2b_order_income
    from orders.models import B2BOrder, B2BPayment

    amount = q2(amount)
    B2BPayment.objects.create(
        company_id=order.company_id, b2b_order=order, amount=amount,
        payment_type=payment_type, payment_method=method, reference_number=(reference or '')[:100],
        notes=notes, payment_date=payment_date or billing.ist_today(),
        created_by=user if getattr(user, 'pk', None) else None,
    )
    sign = Decimal('-1') if payment_type == 'refund' else Decimal('1')
    order.paid_amount = order.paid_amount + sign * amount
    order.balance_amount = order.total_amount - order.paid_amount
    if order.paid_amount >= order.total_amount:
        order.payment_status = B2BOrder.PaymentStatus.PAID
    elif order.paid_amount > 0:
        order.payment_status = B2BOrder.PaymentStatus.PARTIAL
    else:
        order.payment_status = B2BOrder.PaymentStatus.PENDING
    order.save(update_fields=['paid_amount', 'balance_amount', 'payment_status', 'updated_at'])
    sync_b2b_order_income(order)
    actor = user if getattr(user, 'pk', None) else order.created_by
    if actor is not None:
        B2BActivity.objects.create(
            company_id=order.company_id, admin_user=actor, order=order, type=B2BActivity.Type.PAYMENT,
            subject=f'{payment_type.capitalize()} of {amount:.2f} via {method}',
            description=f'Against order {order.pk}' + (f' — {notes}' if notes else ''),
        )


# ── WhatsApp ─────────────────────────────────────────────────────────────

def normalise_number(raw):
    digits = re.sub(r'\D', '', raw or '')
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in '6789':
        return '91' + digits
    return None


def whatsapp_recipient(order):
    """(number '91XXXXXXXXXX', None) or (None, reason)."""
    if kind_of(order) == 'b2c':
        number = normalise_number(order.mobile)
        return (number, None) if number else (None, 'The order has no valid mobile number.')
    candidates = []
    if order.contact_id and order.contact:
        candidates.append(order.contact.phone)
    primary = order.company.contacts.filter(is_active=True).order_by('-is_primary', 'id')
    candidates += [c.phone for c in primary]
    for raw in candidates:
        number = normalise_number(raw)
        if number:
            return number, None
    return None, 'No contact with a valid mobile number on this order or company.'


def message_template(kind):
    key = f'wa_message_{kind}'
    from core.models import Setting

    value = Setting.objects.filter(setting_key=key).values_list('setting_value', flat=True).first()
    return value or DEFAULT_MESSAGES[key]


class _Blank(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def build_message(order, kind, url):
    from core.models import Brand

    brand = Brand.objects.filter(id=order.brand_id).values_list('name', flat=True).first() or ''
    if kind_of(order) == 'b2c':
        name = order.name
        due_date = ''
    else:
        name = order.contact.name if order.contact_id and order.contact else order.company.company_name
        due_date = order.due_date.strftime('%d-%m-%Y') if order.due_date else 'receipt'
    due = amount_due(order)
    return message_template(kind).format_map(_Blank(
        name=name, brand=brand, order_id=order.pk, amount=f'{due:,.2f}', due_date=due_date, link=url,
    ))


def prepare_whatsapp(order, kind, user, request=None):
    """Create the share link if needed, log the click and return the wa.me
    URL to open."""
    if kind not in (MessageLog.Kind.DELIVERY, MessageLog.Kind.REMINDER):
        raise billing.BillingError('Unknown message type.')
    number, reason = whatsapp_recipient(order)
    if not number:
        raise billing.BillingError(reason)
    link = get_or_create_link(order, user)
    text = build_message(order, kind, link_url(link, request))
    label = MessageLog.Kind(kind).label
    MessageLog.objects.create(kind=kind, recipient=number, message=text,
                              created_by=user if getattr(user, 'pk', None) else None, **_ref(order))
    if kind_of(order) == 'b2b':
        from crm.models import B2BActivity

        actor = user if getattr(user, 'pk', None) else order.created_by
        if actor is not None:
            B2BActivity.objects.create(
                company_id=order.company_id, contact=order.contact, admin_user=actor, order=order,
                type=B2BActivity.Type.WHATSAPP, subject=label, description=text,
            )
    billing._activity(user, label, f'Order {order.pk} to {number}', module='orders')
    return {'url': f'https://wa.me/{number}?text={urllib.parse.quote(text)}', 'recipient': number,
            'message': text, 'link': link_url(link, request)}


def last_reminded(order):
    row = MessageLog.objects.filter(kind=MessageLog.Kind.REMINDER, **_ref(order)).order_by('-created_at').first()
    return row.created_at if row else None


def share_payload(order, request=None):
    """Share panel + WhatsApp state for admin screens."""
    link = live_link(order)
    number, reason = whatsapp_recipient(order)
    messages = MessageLog.objects.filter(**_ref(order)).select_related('created_by')[:20]
    return {
        'link': link_url(link, request) if link else None,
        'view_count': link.view_count if link else 0,
        'last_viewed_at': link.last_viewed_at.isoformat() if link and link.last_viewed_at else None,
        'whatsapp_number': number,
        'whatsapp_disabled_reason': reason,
        'amount_due': float(amount_due(order)),
        'messages': [{
            'kind': m.kind, 'label': m.get_kind_display(), 'recipient': m.recipient,
            'by': m.created_by.name if m.created_by else '', 'at': m.created_at.isoformat(),
        } for m in messages],
    }


def delivery_area(order):
    """City / state only — the public page never shows the full address."""
    if kind_of(order) == 'b2c':
        return ', '.join(p for p in (state_name(order.shipping_state_code), order.pincode) if p)
    if not order.ship_to_same_as_bill_to:
        return ', '.join(p for p in (state_name(order.ship_to_state_code), order.ship_to_pincode) if p)
    company = order.company
    return ', '.join(p for p in (company.city, state_name(company.state_code) or company.state) if p)

