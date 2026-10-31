from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0006_phase2"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="gift_note",
            field=models.TextField(blank=True, default=""),
        ),
    ]
