import re

from django.db import migrations

ORDER_RE = re.compile(r'[Oo]rder (\S+)')


def backfill(apps, schema_editor):
    B2BActivity = apps.get_model('crm', 'B2BActivity')
    B2BOrder = apps.get_model('orders', 'B2BOrder')

    activities = B2BActivity.objects.filter(
        type__in=['order', 'payment', 'return'], order__isnull=True,
    )
    for activity in activities:
        text = f'{activity.subject} {activity.description}'
        match = ORDER_RE.search(text)
        if not match:
            continue
        order_id = match.group(1).rstrip('.,')
        if B2BOrder.objects.filter(id=order_id).exists():
            activity.order_id = order_id
            activity.save(update_fields=['order'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0004_b2bactivity_order'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
