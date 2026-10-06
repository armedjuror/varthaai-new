"""
Visit logging — always tied to the user's own *open sales* session, and
lead creation exposes only the minimum B2BCompany fields an employee needs
(never full B2B records: no credit limit, discounts, GST, notes, etc).

Every visit is mirrored into the B2B CRM as a `B2BActivity` (type='visit'),
so it shows up on that company's Timeline tab exactly like a manually
logged call/meeting — the field-tracking and CRM-pipeline views of a
company's history are kept in sync, not two disconnected records of the
same event. A visit that isn't the one that created the lead also nudges
the company from `lead` to `contacted` (never further — anything already
past `lead` stays wherever an admin or an order has put it), recorded as
its own `stage_change` activity, identical to how the admin UI logs one.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Brand
from crm.models import B2BActivity, B2BCompany
from sessions_tracking.models import Session, Visit
from sessions_tracking.services.sessions import get_open_session
from sessions_tracking.timeutil import ist_now

_PURPOSE_LABELS = {
    Visit.Purpose.NEW_LEAD: 'New Lead',
    Visit.Purpose.RETARGET: 'Retarget',
    Visit.Purpose.DELIVERY: 'Delivery',
    Visit.Purpose.COLLECTION: 'Collection',
    Visit.Purpose.AUDIT: 'Audit',
    Visit.Purpose.REORDER_PUSH: 'Reorder Push',
}


def _default_brand():
    brand = Brand.objects.filter(is_active=True).order_by('id').first()
    if not brand:
        raise ValidationError('No active brand configured — ask an admin to set one up.')
    return brand


def _resolve_company(data):
    company_id = data.get('company_id')
    if company_id:
        company = B2BCompany.objects.filter(id=company_id, is_active=True).first()
        if not company:
            return None, ValidationError('Company not found.')
        return company, None

    new_company = data.get('new_company') or {}
    name = (new_company.get('company_name') or '').strip()
    if not name:
        return None, ValidationError('Pick a company, or provide a company_name for a new lead.')

    brand_id = new_company.get('brand_id')
    brand = Brand.objects.filter(id=brand_id, is_active=True).first() if brand_id else _default_brand()
    if brand_id and not brand:
        return None, ValidationError('Invalid brand.')

    company = B2BCompany.objects.create(
        brand=brand,
        company_name=name,
        city=(new_company.get('city') or '').strip(),
        state=(new_company.get('state') or '').strip(),
        pincode=(new_company.get('pincode') or '').strip(),
        address=(new_company.get('address') or '').strip(),
        stage=B2BCompany.Stage.LEAD,
        source='field_visit',
        assigned_to=data.get('_user'),
    )
    return company, None


def _log_visit_activity(company, user, visit):
    label = _PURPOSE_LABELS.get(visit.purpose, visit.purpose)
    subject = f'Field visit — {label}'
    if visit.outcome != Visit.Outcome.NONE:
        subject += f' ({visit.outcome.replace("_", " ")})'
    B2BActivity.objects.create(
        company=company, admin_user=user, type=B2BActivity.Type.VISIT,
        subject=subject, description=visit.notes,
    )

    # A visit means contact was made. Bump a still-untouched lead forward —
    # but never on the very visit that just created it, and never move a
    # company that's already past "lead" (an admin or a real order already
    # decided its stage; a field visit shouldn't walk that back or skip it).
    if visit.purpose != Visit.Purpose.NEW_LEAD and company.stage == B2BCompany.Stage.LEAD:
        old_stage = company.stage
        company.stage = B2BCompany.Stage.CONTACTED
        company.save(update_fields=['stage', 'updated_at'])
        B2BActivity.objects.create(
            company=company, admin_user=user, type=B2BActivity.Type.STAGE_CHANGE,
            subject=f'Stage changed from {old_stage} to {company.stage}',
            old_stage=old_stage, new_stage=company.stage,
        )


@transaction.atomic
def create_visit(user, data):
    session = get_open_session(user)
    if not session or session.type != Session.Type.SALES:
        raise ValidationError('Start a sales session before logging a visit.')

    purpose = data.get('purpose')
    if purpose not in Visit.Purpose.values:
        raise ValidationError('Invalid visit purpose.')
    outcome = data.get('outcome') or Visit.Outcome.NONE
    if outcome not in Visit.Outcome.values:
        raise ValidationError('Invalid visit outcome.')

    data = {**data, '_user': user}
    company, error = _resolve_company(data)
    if error:
        raise error

    visit = Visit.objects.create(
        session=session,
        company=company,
        user=user,
        visited_at=ist_now(),
        purpose=purpose,
        outcome=outcome,
        notes=(data.get('notes') or '').strip(),
    )
    _log_visit_activity(company, user, visit)
    return visit


def company_picker_results(query):
    """Minimal fields for an employee's lead/company picker — never a full
    B2BCompany record (no financials, GST, notes, credit limit, etc)."""
    qs = B2BCompany.objects.filter(is_active=True)
    if query:
        qs = qs.filter(company_name__icontains=query)
    return list(
        qs.order_by('company_name')[:20].values('id', 'company_name', 'stage', 'city'),
    )
