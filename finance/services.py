"""
Order -> Income sync.

Whenever a B2C `orders.Order` or B2B `orders.B2BOrder` becomes fully paid
(and isn't cancelled/deleted), book a row in `finance.Income`; if it stops
being paid (refunded, or cancelled after payment), remove the row again.
`update_or_create`/`delete` keyed off the order make this idempotent, so
it's safe to call after every order mutation and safe to re-run in bulk via
the `backfill_income` management command.
"""
from django.db.models import ExpressionWrapper, F, FloatField, Sum

from finance.models import Income

# order_item.quantity (grams) * sale_price_per_kg / 1000 == rupees for the line.
B2C_REVENUE = ExpressionWrapper(
    F('quantity') * F('sale_price_per_kg') / 1000.0,
    output_field=FloatField(),
)

B2C_INACTIVE_STATUSES = ('cancelled', 'deleted')
B2B_INACTIVE_STATUSES = ('cancelled',)


def sync_b2c_order_income(order):
    """Create/refresh/remove the Income row for a B2C `orders.Order`."""
    is_income = order.payment_status == 'paid' and order.status not in B2C_INACTIVE_STATUSES
    if not is_income:
        Income.objects.filter(order=order).delete()
        return

    revenue = order.items.aggregate(r=Sum(B2C_REVENUE))['r'] or 0.0
    income_date = (order.payment_date or order.order_date).date()
    Income.objects.update_or_create(
        order=order,
        defaults={
            'brand_id': order.brand_id,
            'source_type': Income.SourceType.B2C_ORDER,
            'amount': revenue,
            'income_date': income_date,
        },
    )


def sync_b2b_order_income(order):
    """Create/refresh/remove the Income row for a `orders.B2BOrder`."""
    is_income = order.payment_status == 'paid' and order.status not in B2B_INACTIVE_STATUSES
    if not is_income:
        Income.objects.filter(b2b_order=order).delete()
        return

    last_payment_date = (
        order.payments
        .order_by('-payment_date', '-id')
        .values_list('payment_date', flat=True)
        .first()
    )
    income_date = last_payment_date or order.order_date.date()
    Income.objects.update_or_create(
        b2b_order=order,
        defaults={
            'brand_id': order.brand_id,
            'source_type': Income.SourceType.B2B_ORDER,
            'amount': order.total_amount,
            'income_date': income_date,
        },
    )
