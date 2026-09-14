# Blogs become brand-scoped (previously global). Existing rows are assigned
# to brand 1 (Varthaai) as the one-off default for this backfill.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('marketing', '0002_blog_ai_assistant'),
    ]

    operations = [
        migrations.AddField(
            model_name='blog',
            name='brand',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE, related_name='blogs', to='core.brand'),
            preserve_default=False,
        ),
    ]
