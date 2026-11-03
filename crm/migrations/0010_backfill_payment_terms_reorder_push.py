from django.db import migrations


def forwards(apps, schema_editor):
    B2BActivity = apps.get_model('crm', 'B2BActivity')
    B2BCompany = apps.get_model('crm', 'B2BCompany')

    # "Reorder push" folded into audit.
    B2BActivity.objects.filter(visit_purpose='reorder_push').update(visit_purpose='audit')

    # Old terms were a single "days" number: N > 0 meant the balance was due
    # N days after the order; 0 meant pay in full on delivery.
    B2BCompany.objects.filter(payment_terms_days__gt=0).update(balance_rule='after_days')
    B2BCompany.objects.filter(payment_terms_days__lte=0).update(
        upfront_percent=100, upfront_due='delivery', payment_terms_days=30,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0009_location_coords_pack_counts_payment_terms'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
