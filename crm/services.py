"""Business logic for B2BActivity that isn't plain CRUD — currently just
field-visit logging, which (a) always has structured purpose/outcome, not
free-text subject/description, and (b) can nudge a still-untouched lead
forward a stage."""
from django.db import transaction

from crm.models import B2BActivity, B2BCompany

_VISIT_PURPOSE_LABELS = dict(B2BActivity.VisitPurpose.choices)


@transaction.atomic
def log_visit(user, company, purpose, outcome=None, notes=''):
    outcome = outcome or B2BActivity.VisitOutcome.NONE
    label = _VISIT_PURPOSE_LABELS.get(purpose, purpose)
    subject = f'Field visit — {label}'
    if outcome != B2BActivity.VisitOutcome.NONE:
        subject += f' ({outcome.replace("_", " ")})'

    activity = B2BActivity.objects.create(
        company=company, admin_user=user, type=B2BActivity.Type.VISIT,
        visit_purpose=purpose, visit_outcome=outcome,
        subject=subject, description=notes,
    )
    company.save(update_fields=['updated_at'])

    # A visit means contact was made — bump a still-untouched lead forward,
    # but never on the very visit that created it, and never walk back a
    # company already past "lead" (an admin or a real order already decided
    # that; a field visit shouldn't regress or skip it).
    if purpose != B2BActivity.VisitPurpose.NEW_LEAD and company.stage == B2BCompany.Stage.LEAD:
        old_stage = company.stage
        company.stage = B2BCompany.Stage.CONTACTED
        company.save(update_fields=['stage', 'updated_at'])
        B2BActivity.objects.create(
            company=company, admin_user=user, type=B2BActivity.Type.STAGE_CHANGE,
            subject=f'Stage changed from {old_stage} to {company.stage}',
            old_stage=old_stage, new_stage=company.stage,
        )
    return activity
