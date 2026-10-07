"""Backfill visit_purpose/visit_outcome on existing B2BActivity(type=visit)
rows from sessions_tracking.Visit — every Visit has always created exactly
one mirrored B2BActivity in the same request (see the former
sessions_tracking.services.visits._log_visit_activity), so within a given
(company, user) pair the Nth Visit (by id) pairs with the Nth visit-type
B2BActivity (by id). sessions_tracking.Visit is dropped in a later
migration once this backfill has run."""
from collections import defaultdict

from django.db import migrations


def backfill(apps, schema_editor):
    Visit = apps.get_model('sessions_tracking', 'Visit')
    B2BActivity = apps.get_model('crm', 'B2BActivity')

    visits_by_group = defaultdict(list)
    for v in Visit.objects.order_by('id'):
        visits_by_group[(v.company_id, v.user_id)].append(v)

    activities_by_group = defaultdict(list)
    for a in B2BActivity.objects.filter(type='visit').order_by('id'):
        activities_by_group[(a.company_id, a.admin_user_id)].append(a)

    for key, visits in visits_by_group.items():
        activities = activities_by_group.get(key, [])
        for visit, activity in zip(visits, activities):
            activity.visit_purpose = visit.purpose
            activity.visit_outcome = visit.outcome
            activity.save(update_fields=['visit_purpose', 'visit_outcome'])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0006_b2bactivity_visit_purpose_outcome'),
        ('sessions_tracking', '0002_session_area'),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
