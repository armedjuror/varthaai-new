"""
Adds ContentPlan.brand — ContentPlan is brand-scoped; PlanItem reaches
brand via `plan__brand` rather than duplicating the FK (see content/models.py).

Written by hand instead of via `makemigrations` because the interactive
"provide a one-off default" prompt needs a TTY. Safe either way: the
`content_plans` table has zero rows at the time this migration was
authored (Phase 0 shipped no plan-creation code yet), so the default
below is never actually written to a real row — it only satisfies the
NOT NULL constraint Postgres needs while adding the column.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
        ('content', '0002_seed_series'),
    ]

    operations = [
        migrations.AddField(
            model_name='contentplan',
            name='brand',
            field=models.ForeignKey(
                default=1, on_delete=django.db.models.deletion.CASCADE,
                related_name='content_plans', to='core.brand',
            ),
            preserve_default=False,
        ),
    ]
