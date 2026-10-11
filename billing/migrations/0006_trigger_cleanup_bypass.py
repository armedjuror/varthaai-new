"""
Let the gst_test_cleanup command remove test documents without ever turning
the immutability trigger off: it sets the transaction-local flag
`varthaai.gst_cleanup` = 'on' (SET LOCAL), which ends with its transaction.
"""
from django.db import migrations

FUNCTION = """
CREATE OR REPLACE FUNCTION gst_document_guard() RETURNS trigger AS $$
DECLARE
    allowed text[] := TG_ARGV;
BEGIN
    IF coalesce(current_setting('varthaai.gst_cleanup', true), '') = 'on' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Issued GST documents cannot be deleted (table %).', TG_TABLE_NAME;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'Issued GST documents cannot be edited (table %).', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(FUNCTION)


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0005_immutability_triggers'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
