import django.db.models.deletion
from django.db import migrations, models


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
    ]
