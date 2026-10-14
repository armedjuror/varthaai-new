"""
B2C refunds (Phase 2B): money returned after a credit note, through
Razorpay (refund on the original payment) or recorded as a manual refund.

payment_status follows the refunds: refund_initiated while a Razorpay refund
is pending, refunded once everything paid has been refunded; a partial
refund leaves the order 'paid'.
"""
import logging
from decimal import Decimal

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


def can_refund_online(order):
    return bool(order.razorpay_payment_id and settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


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
            if not can_refund_online(order):
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
        elif method == Refund.Method.MANUAL:
            refund = Refund.objects.create(
                order=order, credit_note=credit_note, amount=amount, method=method,
                status=Refund.Status.PROCESSED, reference=(reference or '')[:100], notes=notes,
                processed_at=timezone.now(), created_by=user if getattr(user, 'pk', None) else None,
            )
        else:
            raise billing.BillingError('Choose Razorpay or manual refund.')
        sync_payment_status(order)
        billing._activity(user, f'Refund ₹{amount} ({refund.get_method_display()})',
                          f'Order {order.id}, status {refund.status}', module='orders')
    return refund


def on_refund_event(razorpay_refund_id, processed):
    """Razorpay webhook: refund.processed / refund.failed."""
    if not razorpay_refund_id:
        return
    with transaction.atomic():
        refund = Refund.objects.select_for_update().filter(razorpay_refund_id=razorpay_refund_id).first()
        if refund is None or refund.status != Refund.Status.INITIATED:
            return
        refund.status = Refund.Status.PROCESSED if processed else Refund.Status.FAILED
        refund.processed_at = timezone.now()
        refund.save(update_fields=['status', 'processed_at'])
        sync_payment_status(refund.order)


def refunds_payload(order):
    return {
        'paid_total': float(paid_total(order)),
        'refundable': float(refundable(order)),
        'can_refund_online': can_refund_online(order),
        'refunds': [{
            'id': r.id, 'amount': float(r.amount), 'method': r.get_method_display(), 'status': r.status,
            'status_label': r.get_status_display(), 'credit_note': r.credit_note.number if r.credit_note_id else '',
            'reference': r.razorpay_refund_id or r.reference, 'date': r.created_at.isoformat(),
        } for r in order.refunds.select_related('credit_note')],
    }
