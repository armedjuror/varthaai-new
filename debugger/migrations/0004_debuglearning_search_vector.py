from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.db import migrations


def _backfill_search_vector(apps, schema_editor):
    DebugLearning = apps.get_model('debugger', 'DebugLearning')
    # Historical models have no custom save(), so populate the column
    # directly via a bulk UPDATE using the same expression
    # debugger.models.DebugLearning.save() applies on every future write.
    DebugLearning.objects.all().update(
        search_vector=SearchVector('title', weight='A') + SearchVector('content', weight='B')
    )


def _noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('debugger', '0003_debugrequest_base_sha'),
    ]

    operations = [
        migrations.AddField(
            model_name='debuglearning',
            name='search_vector',
            field=SearchVectorField(blank=True, editable=False, null=True),
        ),
        migrations.AddIndex(
            model_name='debuglearning',
            index=GinIndex(fields=['search_vector'], name='debug_learning_search_gin'),
        ),
        migrations.RunPython(_backfill_search_vector, _noop_reverse),
    ]
