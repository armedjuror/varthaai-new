"""
Order management on My Day — status updates and payment recording for the
employee's own B2B orders, scoped to orders they created.

Deliberately thin: every mutation is delegated to
orders.views_b2b.B2BOrdersAPI's existing handlers (`_update_status`,
`_add_payment`) — the same methods the desktop B2B Orders screen uses, so
stock deduction, delivered_at, income sync, and B2BActivity logging stay in
one place rather than being re-implemented here (orders/views_b2b.py already
calls `_add_payment` the same way from `_confirm_order` — this mirrors an
existing internal convention, not a new one).
"""
from django.core.exceptions import ValidationError

from orders.models import B2BOrder
from orders.views_b2b import B2BOrdersAPI

OPEN_STATUSES = ('draft', 'confirmed', 'dispatched')

_api = B2BOrdersAPI()


def my_orders(user, brand_id, status=None):
    qs = B2BOrder.objects.filter(brand_id=brand_id, created_by=user).select_related('company')
    if status == 'open':
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif status:
        qs = qs.filter(status=status)
    return qs.order_by('-order_date')[:50]


def _get_owned_order(user, brand_id, order_id):
    order = B2BOrder.objects.filter(id=order_id, brand_id=brand_id, created_by=user).first()
    if not order:
        raise ValidationError('Order not found.')
    return order


def update_order_status(request, brand_id, order_id, new_status):
    _get_owned_order(request.user, brand_id, order_id)  # ownership check
    if new_status not in ('confirmed', 'dispatched', 'delivered', 'cancelled'):
        raise ValidationError('Invalid status.')
    response = _api._update_status(request, brand_id, {'order_id': order_id, 'status': new_status})
    if not response.data.get('success'):
        raise ValidationError(response.data.get('message') or 'Could not update status.')
    return response.data


def record_order_payment(request, brand_id, order_id, body):
    order = _get_owned_order(request.user, brand_id, order_id)
    response = _api._add_payment(request, brand_id, {
        'company_id': order.company_id,
        'order_id': order.id,
        'amount': body.get('amount'),
        'payment_method': body.get('payment_method'),
        'payment_type': 'payment',
        'reference_number': body.get('reference_number') or '',
        'notes': body.get('notes') or '',
        'payment_date': body.get('payment_date') or None,
    })
    if not response.data.get('success'):
        raise ValidationError(response.data.get('message') or 'Could not record payment.')
    return response.data
