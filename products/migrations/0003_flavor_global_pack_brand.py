import django.db.models.deletion
from django.db import migrations, models


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
        ('core', '0001_initial'),
        ('products', '0002_alter_flavorpack_unique_together'),
    ]

    operations = [
        migrations.AddField(
            model_name='flavorpack',
            name='brand',
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='flavor_packs', to='core.brand',
            ),
        ),
        migrations.RunPython(repoint_and_delete_duplicates, noop_reverse),
    ]
