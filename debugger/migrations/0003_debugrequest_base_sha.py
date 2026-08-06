from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('debugger', '0002_debugrequest_learning_draft_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='debugrequest',
            name='base_sha',
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
