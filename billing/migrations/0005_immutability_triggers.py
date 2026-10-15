"""
Database-level guard: issued invoices, credit notes and their lines cannot
be deleted, and only the listed columns may change after issue (status on
cancel & reissue, stored PDF, e-way bill / IRN fields, and the SET NULL links
from invoice lines to order items). The app already refuses this in Python
(billing.models.ImmutableModel); the trigger also covers raw SQL and the
Django admin shell. PostgreSQL only.
"""
from django.db import migrations

TABLES = {
    'invoices': ['status', 'pdf_file', 'pdf_sha256', 'eway_bill_no', 'eway_bill_date', 'irn', 'ack_no',
                 'ack_date', 'signed_qr'],
    'invoice_lines': ['b2c_item_id', 'b2b_item_id'],
    'credit_notes': ['pdf_file', 'pdf_sha256', 'irn', 'ack_no', 'ack_date'],
    'credit_note_lines': [],
}

FUNCTION = """
CREATE OR REPLACE FUNCTION gst_document_guard() RETURNS trigger AS $$
DECLARE
    allowed text[] := TG_ARGV;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Issued GST documents cannot be deleted (table %%).', TG_TABLE_NAME;
    END IF;
    IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
        RAISE EXCEPTION 'Issued GST documents cannot be edited (table %%).', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def forwards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FUNCTION.replace('%%', '%'))
        for table, allowed in TABLES.items():
            args = ', '.join(f"'{c}'" for c in allowed)
            cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
            cursor.execute(
                f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} '
                f'FOR EACH ROW EXECUTE FUNCTION gst_document_guard({args})'
            )


def backwards(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(f'DROP TRIGGER IF EXISTS {table}_immutable ON {table}')
        cursor.execute('DROP FUNCTION IF EXISTS gst_document_guard()')


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0004_phase2'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
