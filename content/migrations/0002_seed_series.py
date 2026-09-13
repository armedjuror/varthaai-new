"""Seed the six ContentSeries rows from content-generator-plan.md §1/§7.

weekday: 0=Monday..6=Sunday. prep_lead_days/intake_lead_days are days
before planned_date that the Copywriter Agent should act — see §3/§7 of
the plan doc for how these get used once Phase 1/2 land.
"""
from django.db import migrations


SERIES = [
    dict(
        name='Varthaai Varthaanm', slug='varthaanm', format='reel',
        content_type='reel_varthaanm', weekday=4, is_alternate_week=False,
        prep_lead_days=5, intake_lead_days=5,
        skill_slug='varthaai-varthaanm-script',
    ),
    dict(
        name='Varthaai Verdict', slug='verdict', format='reel',
        content_type='reel_verdict', weekday=2, is_alternate_week=False,
        prep_lead_days=3, intake_lead_days=3,
        skill_slug='varthaai-verdict-script',
    ),
    dict(
        name='Learn With Varthaai', slug='learn', format='poster',
        content_type='poster_learn', weekday=None, is_alternate_week=False,
        prep_lead_days=2, intake_lead_days=2,
        skill_slug='learn-with-varthaai-script',
    ),
    dict(
        name='Varthaai Inside', slug='inside', format='reel',
        content_type='reel_inside', weekday=0, is_alternate_week=True,
        prep_lead_days=8, intake_lead_days=8,
        skill_slug='varthaai-inside-questions',
    ),
    dict(
        name='Blog', slug='blog', format='blog',
        content_type='blog', weekday=None, is_alternate_week=False,
        prep_lead_days=None, intake_lead_days=None,
        skill_slug='',
    ),
    dict(
        name='Occasion', slug='occasion', format='poster',
        content_type='poster_occasion', weekday=None, is_alternate_week=False,
        prep_lead_days=None, intake_lead_days=None,
        skill_slug='',
    ),
]


def seed_series(apps, schema_editor):
    ContentSeries = apps.get_model('content', 'ContentSeries')
    for row in SERIES:
        ContentSeries.objects.update_or_create(slug=row['slug'], defaults=row)


def unseed_series(apps, schema_editor):
    ContentSeries = apps.get_model('content', 'ContentSeries')
    ContentSeries.objects.filter(slug__in=[r['slug'] for r in SERIES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_series, unseed_series),
    ]
