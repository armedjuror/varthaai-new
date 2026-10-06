"""
One-off data cleanup for the B2B batch-pinning bug (see products/services.py
and orders/views_b2b.py — B2B order lines pin a specific Stock batch via
`stock_id`, but confirm/cancel used to always deduct/revert the FIFO active
batch instead of the pinned one; fixed in deduct_from_batch/_deduct_order_stock).

Historical StockMovement rows for reference_type='b2b_sale'/'b2b_reversal'
record which batch was ACTUALLY (wrongly) touched; B2BOrderItem.stock_id
records which batch SHOULD have been touched. This command recomputes, per
batch, what its quantity would be had every B2B sale/reversal always targeted
the pinned batch, and applies the difference as an 'adjustment' StockMovement.

Per batch:
    base   = current_quantity
             + (b2b_sale grams that landed on this batch)   [undo the wrong debit]
             - (b2b_reversal grams that landed on this batch) [undo the wrong credit]
             - (b2b_return grams that landed on this batch)   [these already correctly
               target the pinned batch via restock_batch — strip them out of the
               "undo" step so they aren't double-counted against correct_outstanding,
               which already nets returned_quantity out]
    correct_outstanding = sum over this batch's pinned, still-deducted order
             items of (quantity - returned_quantity) * weight_grams
    target = max(base - correct_outstanding, 0)   # never drive a batch negative

Orders with NO items pinned to any batch for a given flavor (legacy fallback,
FIFO-only) are excluded from that flavor's reconciliation — their movements
were correctly attributed already and must not be "undone".

Deleted orders (items gone, no stock_id to recover) are excluded from the
replay entirely; any of their movements are left as sunk history. Known
orphaned movements with no matching counterpart (e.g. a reversal with no sale)
are NOT auto-corrected here — inspect and handle explicitly per
`notes`/reference_id if the dry-run output flags a shortfall that looks larger
than expected.

Because of the above, some batches may have an unrecoverable "shortfall" —
grams that order history says should be deducted from a batch that doesn't
currently hold enough (because, e.g., legitimate B2C sales drew down the same
physical batch in the interim). These are reported but NOT fabricated — the
batch is floored at 0 and the shortfall is printed for visibility.

Usage:
    python manage.py reconcile_b2b_batch_stock --dry-run
    python manage.py reconcile_b2b_batch_stock
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from orders.models import B2BOrder, B2BOrderItem
from products.models import Stock, StockMovement
from products.services import _refresh_alerts

REFERENCE_ID = 'b2b-batch-pin-cleanup'


class Command(BaseCommand):
    help = "Reconcile Stock batch quantities for the B2B batch-pinning deduction bug."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without saving.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']

        existing_order_ids = set(B2BOrder.objects.values_list('id', flat=True))
        unpinned_pairs = set(
            B2BOrderItem.objects.filter(stock_id__isnull=True)
            .values_list('b2b_order_id', 'flavor_id'),
        )

        total_shortfall = 0
        changed = 0

        for batch in Stock.objects.select_related('flavor').order_by('flavor_id', 'id'):
            excluded_orders = {oid for oid, fid in unpinned_pairs if fid == batch.flavor_id}
            valid_order_ids = existing_order_ids - excluded_orders

            moves = StockMovement.objects.filter(
                stock=batch,
                reference_type__in=['b2b_sale', 'b2b_reversal', 'b2b_return'],
                reference_id__in=valid_order_ids,
            )
            sale = moves.filter(reference_type='b2b_sale').aggregate(s=Sum('quantity_grams'))['s'] or 0
            reversal = moves.filter(reference_type='b2b_reversal').aggregate(s=Sum('quantity_grams'))['s'] or 0
            returned = moves.filter(reference_type='b2b_return').aggregate(s=Sum('quantity_grams'))['s'] or 0

            items = B2BOrderItem.objects.filter(stock=batch, b2b_order__stock_deducted=True)
            correct_outstanding = sum(
                (it.quantity - it.returned_quantity) * it.weight_grams for it in items
            )

            base = batch.quantity_grams + sale - reversal - returned
            raw_target = base - correct_outstanding
            target = max(raw_target, 0)
            shortfall = max(-raw_target, 0)
            delta = target - batch.quantity_grams

            if shortfall:
                total_shortfall += shortfall
                self.stdout.write(self.style.WARNING(
                    f'batch #{batch.id} ({batch.flavor.name} {batch.batch_number}): '
                    f'{shortfall}g owed by B2B order history cannot be recovered '
                    f'from this batch without going negative — left at 0.',
                ))

            if delta == 0:
                continue
            changed += 1
            self.stdout.write(
                f'batch #{batch.id} ({batch.flavor.name} {batch.batch_number}): '
                f'{batch.quantity_grams}g -> {target}g ({delta:+d}g)',
            )
            if not dry_run:
                prev = batch.quantity_grams
                batch.quantity_grams = target
                batch.save(update_fields=['quantity_grams'])
                StockMovement.objects.create(
                    flavor=batch.flavor, stock=batch,
                    movement_type=(
                        StockMovement.MovementType.IN if delta > 0
                        else StockMovement.MovementType.OUT
                    ),
                    quantity_grams=abs(delta), previous_quantity_grams=prev, new_quantity_grams=target,
                    reference_type='adjustment', reference_id=REFERENCE_ID,
                    notes=(
                        'B2B batch-pinning bug cleanup — correcting historical '
                        'mis-attributed FIFO deductions back to the batch each '
                        'order line was actually pinned to.'
                    ),
                )
                _refresh_alerts(batch.flavor)

        verb = 'Would adjust' if dry_run else 'Adjusted'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {changed} batch(es). Total unrecoverable shortfall: {total_shortfall}g.',
        ))

        self._write_off_phantom_reversal(dry_run)

        if dry_run:
            transaction.set_rollback(True)

    def _write_off_phantom_reversal(self, dry_run):
        """
        One known orphaned movement, unrelated to the batch-pinning bug: order
        MC_B_00a52708e6024 (deleted, so its items/history can't be re-derived)
        has StockMovement #248 — an `in b2b_reversal` of 150g on this batch
        with no matching `out b2b_sale` anywhere in history. It's a phantom
        credit inflating available stock and is written off here explicitly
        rather than folded into the bulk reconciliation above.
        """
        PHANTOM_MOVEMENT_ID = 248
        PHANTOM_ORDER_ID = 'MC_B_00a52708e6024'
        PHANTOM_GRAMS = 150

        movement = StockMovement.objects.filter(
            id=PHANTOM_MOVEMENT_ID, reference_id=PHANTOM_ORDER_ID,
            reference_type='b2b_reversal', quantity_grams=PHANTOM_GRAMS,
        ).select_related('stock', 'flavor').first()
        if not movement or not movement.stock:
            self.stdout.write(self.style.WARNING(
                'Phantom reversal write-off skipped — movement #248 no longer '
                'matches the expected shape (already handled, or data changed).',
            ))
            return

        batch = movement.stock
        if batch.quantity_grams < PHANTOM_GRAMS:
            self.stdout.write(self.style.WARNING(
                f'Phantom reversal write-off skipped — batch #{batch.id} only has '
                f'{batch.quantity_grams}g, less than the {PHANTOM_GRAMS}g to write off.',
            ))
            return

        self.stdout.write(
            f'Writing off phantom {PHANTOM_GRAMS}g credit on batch #{batch.id} '
            f'({batch.flavor.name} {batch.batch_number}), from deleted order {PHANTOM_ORDER_ID}.',
        )
        if dry_run:
            return

        prev = batch.quantity_grams
        batch.quantity_grams = prev - PHANTOM_GRAMS
        batch.save(update_fields=['quantity_grams'])
        StockMovement.objects.create(
            flavor=batch.flavor, stock=batch, movement_type=StockMovement.MovementType.OUT,
            quantity_grams=PHANTOM_GRAMS, previous_quantity_grams=prev, new_quantity_grams=batch.quantity_grams,
            reference_type='adjustment', reference_id=f'{REFERENCE_ID}-phantom-writeoff',
            notes=(
                f'Write-off: phantom 150g b2b_reversal (movement #{PHANTOM_MOVEMENT_ID}) '
                f'from deleted order {PHANTOM_ORDER_ID} had no matching sale.'
            ),
        )
        _refresh_alerts(batch.flavor)
