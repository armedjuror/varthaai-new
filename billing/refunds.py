"""
B2C refunds (Phase 2B): money returned after a credit note, through the
gateway that took the payment (Razorpay or Cashfree — a refund on the original
payment) or recorded as a manual refund.

payment_status follows the refunds: refund_initiated while a gateway refund
is pending, refunded once everything paid has been refunded; a partial
refund leaves the order 'paid'.
"""
import logging
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing import services as billing
from billing.models import Refund
from billing.tax import ZERO, q2

log = logging.getLogger(__name__)

PAID_STATES = ('paid', 'refund_initiated', 'refunded')


def paid_total(order):
    return billing.b2c_order_total(order) if order.payment_status in PAID_STATES else ZERO


def refunded_total(order, statuses=(Refund.Status.INITIATED, Refund.Status.PROCESSED)):
    return order.refunds.filter(status__in=statuses).aggregate(t=Sum('amount'))['t'] or ZERO


def refundable(order):
    return max(ZERO, q2(paid_total(order) - refunded_total(order)))


def can_refund_razorpay(order):
    return bool(order.razorpay_payment_id and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def can_refund_cashfree(order):
    return bool(order.cashfree_payment_id and settings.CASHFREE_APP_ID and settings.CASHFREE_SECRET_KEY)


def cashfree_order_ref(order):
    """The Cashfree order the payment was made on: the storefront checkout
    uses our order id; a payment from the order link / pay page has its own
    Cashfree order id (VP...)."""
    from billing.models import PaymentAttempt

    attempt = (PaymentAttempt.objects.filter(b2c_order=order, gateway='cashfree', status='paid',
                                            cashfree_payment_id=order.cashfree_payment_id)
               .exclude(cashfree_order_id__isnull=True).order_by('-id').first())
    return attempt.cashfree_order_id if attempt else order.id


def online_refund_method(order):
    """The gateway refund method for this order's payment, or ''."""
    if can_refund_cashfree(order):
        return Refund.Method.CASHFREE
    if can_refund_razorpay(order):
        return Refund.Method.RAZORPAY
    return ''


def can_refund_online(order):
    return bool(online_refund_method(order))


def sync_payment_status(order):
    """Derive refund_initiated / refunded from the refunds on the order."""
    from finance.services import sync_b2c_order_income

    if order.payment_status not in PAID_STATES:
        return
    pending = order.refunds.filter(status=Refund.Status.INITIATED).exists()
    processed = refunded_total(order, (Refund.Status.PROCESSED,))
    paid = paid_total(order)
    if paid > 0 and processed >= paid:
        new = 'refunded'
    elif pending:
        new = 'refund_initiated'
    elif order.status == 'cancelled' and processed < paid:
        new = 'refund_initiated'   # fully credited, money still to go back
    else:
        new = 'paid'
    if new != order.payment_status:
        order.payment_status = new
        order.save(update_fields=['payment_status', 'updated_at'])
    sync_b2c_order_income(order)


def issue_refund(order, amount, method, user=None, credit_note=None, reference='', notes=''):
    """Refund `amount` (never more than is refundable). Razorpay refunds are
    created on the order's original payment; manual ones are recorded as
    processed straight away."""
    amount = q2(Decimal(str(amount)))
    if amount <= 0:
        raise billing.BillingError('Refund amount must be positive.')
    with transaction.atomic():
        order = type(order).objects.select_for_update().get(pk=order.pk)
        available = refundable(order)
        if amount > available:
            raise billing.BillingError(f'Only ₹{available:,.2f} can still be refunded on this order.')
        if method == Refund.Method.RAZORPAY:
            if not can_refund_razorpay(order):
                raise billing.BillingError('This order has no Razorpay payment to refund. Record a manual refund.')
            import razorpay

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            try:
                rzp = client.payment.refund(order.razorpay_payment_id, {
                    'amount': int(amount * 100),
                    'notes': {'order_id': order.id, 'credit_note': credit_note.number if credit_note else ''},
                })
            except Exception as exc:  # noqa: BLE001
                log.warning('Razorpay refund failed for %s: %s', order.id, exc)
                raise billing.BillingError(f'Razorpay refund failed: {exc}') from exc
            processed = rzp.get('status') == 'processed'
            refund = Refund.objects.create(
                order=order, credit_note=credit_note, amount=amount, method=method,
                status=Refund.Status.PROCESSED if processed else Refund.Status.INITIATED,
                razorpay_refund_id=rzp.get('id'), notes=notes,
                processed_at=timezone.now() if processed else None,
                created_by=user if getattr(user, 'pk', None) else None,
            )
        elif method == Refund.Method.CASHFREE:
            if not can_refund_cashfree(order):
                raise billing.BillingError('This order has no Cashfree payment to refund. Record a manual refund.')
            from storefront import payments

            refund_id = 'rf' + uuid4().hex[:24]
            try:
                cf = payments.cashfree_refund(cashfree_order_ref(order), amount, refund_id,
                                              note=f'Credit note {credit_note.number}' if credit_note else '')
            except payments.CashfreeError as exc:
                log.warning('Cashfree refund failed for %s: %s', order.id, exc)
                raise billing.BillingError(f'Cashfree refund failed: {exc}') from exc
            processed = cf.get('refund_status') in payments.CASHFREE_REFUND_DONE
            refund = Refund.objects.create(
                order=order, credit_note=credit_note, amount=amount, method=method,
                status=Refund.Status.PROCESSED if processed else Refund.Status.INITIATED,
                cashfree_refund_id=refund_id, reference=str(cf.get('cf_refund_id') or '')[:100], notes=notes,
                processed_at=timezone.now() if processed else None,
                created_by=user if getattr(user, 'pk', None) else None,
            )
        elif method == Refund.Method.MANUAL:
            refund = Refund.objects.create(
                order=order, credit_note=credit_note, amount=amount, method=method,
                status=Refund.Status.PROCESSED, reference=(reference or '')[:100], notes=notes,
                processed_at=timezone.now(), created_by=user if getattr(user, 'pk', None) else None,
            )
        else:
            raise billing.BillingError('Choose an online (original payment) or manual refund.')
        sync_payment_status(order)
        billing._activity(user, f'Refund ₹{amount} ({refund.get_method_display()})',
                          f'Order {order.id}, status {refund.status}', module='orders')
    return refund


def on_refund_event(razorpay_refund_id, processed):
    """Razorpay webhook: refund.processed / refund.failed."""
    if razorpay_refund_id:
        _settle_refund({'razorpay_refund_id': razorpay_refund_id}, processed)


def on_cashfree_refund_event(cashfree_refund_id, processed):
    """Cashfree webhook: REFUND_STATUS_WEBHOOK (SUCCESS / CANCELLED)."""
    if cashfree_refund_id:
        _settle_refund({'cashfree_refund_id': cashfree_refund_id}, processed)


def _settle_refund(lookup, processed):
    with transaction.atomic():
        refund = Refund.objects.select_for_update().filter(**lookup).first()
        if refund is None or refund.status != Refund.Status.INITIATED:
            return
        refund.status = Refund.Status.PROCESSED if processed else Refund.Status.FAILED
        refund.processed_at = timezone.now()
        refund.save(update_fields=['status', 'processed_at'])
        if refund.order_id:
            sync_payment_status(refund.order)
        elif refund.b2b_order_id and not processed:
            _restore_failed_b2b_refund(refund)


# ── B2B: refunds through Cashfree ────────────────────────────────────────
# Only money that came in through Cashfree (the order link / pay page) can go
# back through Cashfree; cash / UPI / bank payments are refunded manually.

def b2b_cashfree_refundable(order):
    """[(paid Cashfree attempt, amount still refundable on it)], newest first,
    never more in total than the order currently shows as paid."""
    from billing.models import PaymentAttempt

    rows, cap = [], max(ZERO, q2(order.paid_amount))
    for attempt in PaymentAttempt.objects.filter(
        b2b_order=order, gateway='cashfree', status=PaymentAttempt.Status.PAID,
    ).exclude(cashfree_order_id__isnull=True).order_by('-id'):
        used = attempt.refunds.filter(
            status__in=(Refund.Status.INITIATED, Refund.Status.PROCESSED)).aggregate(t=Sum('amount'))['t'] or ZERO
        left = min(q2(attempt.amount - used), cap)
        if left > 0:
            rows.append((attempt, left))
            cap -= left
    return rows


def b2b_cashfree_refundable_total(order):
    return sum((left for _, left in b2b_cashfree_refundable(order)), ZERO)


def send_b2b_cashfree_refund(order, amount, user=None, notes=''):
    """Send `amount` back through Cashfree on the order's Cashfree payments
    and book each part as a refund row. Does NOT change the order's paid
    amount — the caller does. Returns the amount actually sent (a later part
    can fail after earlier ones went out; those are kept, never rolled back)."""
    from orders.models import B2BPayment
    from storefront import payments

    amount = q2(Decimal(str(amount)))
    if amount <= 0:
        raise billing.BillingError('Refund amount must be positive.')
    rows = b2b_cashfree_refundable(order)
    available = sum((left for _, left in rows), ZERO)
    if amount > available:
        raise billing.BillingError(
            f'Only ₹{available:,.2f} of this order was paid through Cashfree and can be refunded there. '
            'Refund the rest manually.')
    sent, remaining = ZERO, amount
    actor = user if getattr(user, 'pk', None) else None
    for attempt, left in rows:
        if remaining <= 0:
            break
        part = min(left, remaining)
        refund_id = 'rf' + uuid4().hex[:24]
        try:
            cf = payments.cashfree_refund(attempt.cashfree_order_id, part, refund_id, note=notes or f'Order {order.pk}')
        except payments.CashfreeError as exc:
            log.warning('Cashfree B2B refund failed for %s: %s', order.pk, exc)
            if sent == 0:
                raise billing.BillingError(f'Cashfree refund failed: {exc}') from exc
            break
        processed = cf.get('refund_status') in payments.CASHFREE_REFUND_DONE
        booked = B2BPayment.objects.create(
            company_id=order.company_id, b2b_order=order, amount=part,
            payment_type=B2BPayment.PaymentType.REFUND, payment_method=B2BPayment.PaymentMethod.ONLINE,
            reference_number=refund_id, notes=(notes or 'Refund') + ' (via Cashfree)',
            payment_date=billing.ist_today(), created_by=actor,
        )
        Refund.objects.create(
            b2b_order=order, payment_attempt=attempt, b2b_payment=booked, amount=part,
            method=Refund.Method.CASHFREE, cashfree_refund_id=refund_id,
            status=Refund.Status.PROCESSED if processed else Refund.Status.INITIATED,
            reference=str(cf.get('cf_refund_id') or '')[:100], notes=notes,
            processed_at=timezone.now() if processed else None, created_by=actor,
        )
        sent += part
        remaining -= part
    return sent


def refund_b2b_via_cashfree(order, amount, user=None, notes=''):
    """Admin 'Refund via Cashfree' on a B2B order (e.g. an overpayment):
    sends the refund and lowers the paid amount. Returns the amount sent."""
    from billing.links import set_b2b_paid
    from crm.models import B2BActivity

    with transaction.atomic():
        order = type(order).objects.select_for_update().get(pk=order.pk)
        sent = send_b2b_cashfree_refund(order, amount, user, notes)
        set_b2b_paid(order, order.paid_amount - sent)
        actor = user if getattr(user, 'pk', None) else order.created_by
        if actor is not None:
            B2BActivity.objects.create(
                company_id=order.company_id, admin_user=actor, order=order, type=B2BActivity.Type.PAYMENT,
                subject=f'Refund of {sent:.2f} via Cashfree',
                description=f'Against order {order.pk}' + (f' — {notes}' if notes else ''),
            )
        billing._activity(user, f'Refund ₹{sent} (Cashfree)', f'B2B order {order.pk}', module='b2b')
    return sent


def _restore_failed_b2b_refund(refund):
    """Cashfree could not refund: the money is still with us, so book it back
    on the order (it shows as paid / advance again)."""
    from billing.links import apply_b2b_payment
    from orders.models import B2BOrder

    order = B2BOrder.objects.select_for_update().filter(pk=refund.b2b_order_id).first()
    if order is None:
        return
    apply_b2b_payment(order, refund.amount, method='online', reference=refund.cashfree_refund_id or '',
                      notes=f'Cashfree refund {refund.cashfree_refund_id} failed — amount restored', user=None)


def refunds_payload(order):
    return {
        'paid_total': float(paid_total(order)),
        'refundable': float(refundable(order)),
        'can_refund_online': can_refund_online(order),
        'online_refund_method': online_refund_method(order),
        'refunds': [{
            'id': r.id, 'amount': float(r.amount), 'method': r.get_method_display(), 'status': r.status,
            'status_label': r.get_status_display(), 'credit_note': r.credit_note.number if r.credit_note_id else '',
            'reference': r.razorpay_refund_id or r.reference or r.cashfree_refund_id or '', 'date': r.created_at.isoformat(),
        } for r in order.refunds.select_related('credit_note')],
    }
