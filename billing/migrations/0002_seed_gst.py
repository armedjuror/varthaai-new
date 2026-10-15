"""
Seed GST billing:
  - the one legal entity (Varthaai Foods LLP, Kerala). GSTIN, PAN, bank and
    FSSAI must be filled in Settings -> GST before invoices can be issued;
    address / phone / email / FSSAI start from the existing business settings.
  - link every brand to it.
  - default GST settings (D1, D4).
  - B2BCompany.state_code from the GSTIN prefix or the free-text state.
"""
from django.db import migrations

SETTINGS = [
    ('gst_price_mode_b2b', 'exclusive', 'GST price mode for B2B orders (inclusive/exclusive)'),
    ('gst_price_mode_b2c', 'inclusive', 'GST price mode for B2C orders (inclusive/exclusive)'),
    ('gst_effective_date', '2026-10-01', 'Orders dispatched on/after this date get GST invoices'),
    ('gst_invoice_copies', 'original,duplicate,triplicate', 'Tax invoice copies printed by default'),
]


def forwards(apps, schema_editor):
    from billing.gst_states import normalise_state_code

    LegalEntity = apps.get_model('billing', 'LegalEntity')
    Brand = apps.get_model('core', 'Brand')
    Setting = apps.get_model('core', 'Setting')
    B2BCompany = apps.get_model('crm', 'B2BCompany')

    existing = dict(Setting.objects.values_list('setting_key', 'setting_value'))
    entity = LegalEntity.objects.order_by('id').first()
    if entity is None:
        entity = LegalEntity.objects.create(
            legal_name='Varthaai Foods LLP',
            trade_name='Varthaai',
            state_code='32',
            address=existing.get('business_address', ''),
            phone=existing.get('business_phone', ''),
            email=existing.get('business_email', ''),
            fssai_no=existing.get('fssai_number', '') or existing.get('business_fssai', ''),
            jurisdiction='Kozhikode',
            signatory='Authorised Signatory',
        )
    Brand.objects.filter(legal_entity__isnull=True).update(legal_entity=entity)

    for key, value, description in SETTINGS:
        Setting.objects.get_or_create(
            setting_key=key,
            defaults={'setting_value': value, 'description': description, 'category': 'gst'},
        )

    for company in B2BCompany.objects.all():
        gstin = (company.gst_number or '').strip().upper()
        code = gstin[:2] if len(gstin) == 15 and gstin[:2].isdigit() else ''
        code = normalise_state_code(code) or normalise_state_code(company.state)
        if code:
            B2BCompany.objects.filter(pk=company.pk).update(state_code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
        ('core', '0003_brand_legal_entity'),
        ('crm', '0008_b2bcompany_gst_legal_name_b2bcompany_state_code'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
