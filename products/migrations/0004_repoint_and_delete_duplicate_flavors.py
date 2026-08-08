from django.db import migrations


def repoint_and_delete_duplicates(apps, schema_editor):
    """
    Brand 2 ("Monkey Chips") duplicated two Brand 1 flavors by name before
    Flavor became a global catalog: "Classic Salted" (id 15) and
    "Spicy Sliced" (id 16). Verified via read-only SQL immediately before
    writing this migration: zero rows reference flavor 15/16 anywhere
    (order_items, b2b_order_items, stock, stock_movements, stock_alerts) —
    safe to delete outright.

    Repoint their FlavorPacks onto the canonical Brand-1 flavor rows
    (matched by name), keeping the packs themselves scoped to brand 2 (the
    packaging still belongs to Monkey Chips), then delete the duplicates.

    Kept in its own migration/transaction, separate from the AddField that
    introduces FlavorPack.brand (0003) and the later schema finalization
    (0004_finalize_flavor_global_pack_brand). Postgres refuses to run DDL
    (CREATE INDEX / ALTER TABLE) against a table in the same transaction as
    a still-pending deferred trigger event (e.g. an FK constraint check)
    left behind by a prior DML statement on that same table — it raises
    "cannot CREATE/ALTER ... because it has pending trigger events".
    AddField(FlavorPack.brand) queues a deferred CREATE INDEX for the new
    FK column that only runs when its migration's transaction exits;
    bundling this RunPython's UPDATE/DELETE into that same transaction (as
    the original single 0003 did) put that DML before the deferred index
    creation and tripped the error. Splitting so each migration commits
    its own transaction (AddField first, then this data migration, then
    the NOT NULL / unique_together / RemoveField finalization last) avoids
    it entirely.
    """
    Flavor = apps.get_model('products', 'Flavor')
    FlavorPack = apps.get_model('products', 'FlavorPack')

    duplicate_names = ['Classic Salted', 'Spicy Sliced']
    for name in duplicate_names:
        flavors = list(Flavor.objects.filter(name=name).order_by('id'))
        if len(flavors) < 2:
            continue
        canonical = flavors[0]
        for dup in flavors[1:]:
            FlavorPack.objects.filter(flavor_id=dup.id).update(
                flavor_id=canonical.id, brand_id=dup.brand_id,
            )
            dup.delete()


def noop_reverse(apps, schema_editor):
    # Not reversible: the duplicate Flavor rows and the original
    # flavor->pack linkage are gone. Documented data loss on rollback.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0003_flavor_global_pack_brand'),
    ]

    operations = [
        migrations.RunPython(repoint_and_delete_duplicates, noop_reverse),
    ]
