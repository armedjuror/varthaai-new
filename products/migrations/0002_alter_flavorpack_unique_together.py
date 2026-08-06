from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0001_initial'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='flavorpack',
            unique_together=set(),
        ),
    ]
