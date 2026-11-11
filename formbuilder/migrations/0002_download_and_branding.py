import django.db.models.deletion
import formbuilder.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_brand_legal_entity"),
        ("formbuilder", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="form",
            name="download_file",
            field=models.FileField(
                blank=True, max_length=300,
                storage=formbuilder.models.private_form_storage,
                upload_to=formbuilder.models.download_upload_to,
            ),
        ),
        migrations.AddField(
            model_name="form",
            name="download_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="form",
            name="download_label",
            field=models.CharField(default="Download", max_length=100),
        ),
        migrations.AddField(
            model_name="form",
            name="download_hours",
            field=models.PositiveIntegerField(default=72),
        ),
        migrations.AddField(
            model_name="submission",
            name="download_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="submission",
            name="last_downloaded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="FormBranding",
            fields=[
                ("brand", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                    related_name="form_branding", serialize=False, to="core.brand")),
                ("primary_color", models.CharField(blank=True, max_length=7)),
                ("accent_color", models.CharField(blank=True, max_length=7)),
                ("background_color", models.CharField(blank=True, max_length=7)),
                ("logo", models.ImageField(blank=True, null=True, upload_to="brands/forms/")),
                ("footer_text", models.CharField(blank=True, max_length=200)),
                ("default_download_hours", models.PositiveIntegerField(default=72)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "form_brandings"},
        ),
    ]
