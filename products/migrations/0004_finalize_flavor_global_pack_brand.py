import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0004_repoint_and_delete_duplicate_flavors'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flavorpack',
            name='brand',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='flavor_packs', to='core.brand',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='flavorpack',
            unique_together={('flavor', 'brand', 'label')},
        ),
        migrations.RemoveField(
            model_name='flavor',
            name='brand',
        ),
    ]
