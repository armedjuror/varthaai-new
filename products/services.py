"""
Shared stock service — the single source of truth for FIFO batch deduction,
reversal, availability, and alert refresh. Both B2C order fulfilment and B2B
order confirmation MUST go through here (do not re-implement stock math in the
orders app). Owned by the foundation; order agents import, don't modify.

Business rules (from the PHP app):
  - Each flavor has multiple Stock batches; one is the `is_active_batch`.
  - Deduction draws from the active batch, then auto-activates the next
    non-empty batch (FIFO by id) when one empties.
  - Every change records a StockMovement (audit trail).
  - After any change, low/out-of-stock alerts are refreshed.
"""
from django.db import transaction
from django.db.models import Sum

from products.models import Flavor, Stock, StockAlert, StockMovement


def available_grams(flavor):
    """Unreserved grams available across all of a flavor's batches."""
    agg = flavor.stock_batches.aggregate(
        q=Sum('quantity_grams'), r=Sum('reserved_quantity_grams'),
    )
    return (agg['q'] or 0) - (agg['r'] or 0)


def _ensure_active_batch(flavor):
    """Return a non-empty active batch, activating the FIFO-oldest if needed."""
    flavor.stock_batches.filter(is_active_batch=True, quantity_grams__lte=0).update(is_active_batch=False)
    active = flavor.stock_batches.filter(is_active_batch=True, quantity_grams__gt=0).order_by('id').first()
    if active:
        return active
    nxt = flavor.stock_batches.filter(quantity_grams__gt=0).order_by('id').first()
    if nxt:
        nxt.is_active_batch = True
        nxt.save(update_fields=['is_active_batch'])
    return nxt


@transaction.atomic
def deduct_stock(flavor, grams, *, reference_type, reference_id='', created_by=None, notes=''):
    """
    Deduct `grams` FIFO across batches, recording an 'out' movement per batch.
    Returns True if fully deducted, False if stock ran out (partial deduction
    still recorded — callers that need atomicity should check available_grams
    first inside their own transaction).
    """
    remaining = int(grams)
    while remaining > 0:
        batch = _ensure_active_batch(flavor)
        if batch is None:
            _refresh_alerts(flavor)
            return False
        take = min(batch.quantity_grams, remaining)
        prev = batch.quantity_grams
        batch.quantity_grams = prev - take
        batch.save(update_fields=['quantity_grams'])
        StockMovement.objects.create(
            flavor=flavor, stock=batch, movement_type=StockMovement.MovementType.OUT,
            quantity_grams=take, previous_quantity_grams=prev, new_quantity_grams=batch.quantity_grams,
            reference_type=reference_type, reference_id=str(reference_id),
            created_by=created_by, notes=notes,
        )
        remaining -= take
        if batch.quantity_grams == 0:
            _ensure_active_batch(flavor)
    _refresh_alerts(flavor)
    return True


@transaction.atomic
def revert_stock(flavor, grams, *, reference_type, reference_id='', created_by=None, notes=''):
    """Add `grams` back to the active batch (or FIFO-oldest), recording an 'in' movement."""
    batch = _ensure_active_batch(flavor) or flavor.stock_batches.order_by('id').first()
    if batch is None:
        return False
    prev = batch.quantity_grams
    batch.quantity_grams = prev + int(grams)
    batch.is_active_batch = True
    batch.save(update_fields=['quantity_grams', 'is_active_batch'])
    StockMovement.objects.create(
        flavor=flavor, stock=batch, movement_type=StockMovement.MovementType.IN,
        quantity_grams=int(grams), previous_quantity_grams=prev, new_quantity_grams=batch.quantity_grams,
        reference_type=reference_type, reference_id=str(reference_id),
        created_by=created_by, notes=notes,
    )
    _refresh_alerts(flavor)
    return True


def _refresh_alerts(flavor):
    """Create a low/out-of-stock alert if warranted and none is unacknowledged."""
    available = available_grams(flavor)
    reorder = flavor.reorder_level_grams
    if available <= 0:
        alert_type = StockAlert.AlertType.OUT_OF_STOCK
    elif available <= reorder:
        alert_type = StockAlert.AlertType.LOW_STOCK
    else:
        return
    exists = flavor.alerts.filter(alert_type=alert_type, is_acknowledged=False).exists()
    if not exists:
        StockAlert.objects.create(
            flavor=flavor, alert_type=alert_type,
            current_quantity_grams=max(available, 0), reorder_level_grams=reorder,
        )
