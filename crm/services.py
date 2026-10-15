"""CRM business logic that isn't plain CRUD: field-visit logging, which
(a) always has structured purpose/outcome, not free-text subject/description,
and (b) can nudge a still-untouched lead forward a stage — plus the parsing
helpers shared with the session tracker (per-flavour pack counts) and the
payment-terms rules that set a B2B order's default due date."""
import calendar
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from crm.models import B2BActivity, B2BCompany
from products.models import Flavor

_VISIT_PURPOSE_LABELS = dict(B2BActivity.VisitPurpose.choices)


def clean_flavor_counts(raw, label='packs'):
    """Normalise a {flavour_id: count} mapping (keys may arrive as strings,
    values as numeric strings) to {"<flavour_id>": int}. Counts are whole
    numbers >= 0 — zero is a real reading (an empty shelf, nothing carried).
    Raises ValidationError on anything malformed or an unknown flavour."""
    if not isinstance(raw, dict) or not raw:
        raise ValidationError(f'Enter {label} for each flavour.')
    cleaned = {}
    for key, value in raw.items():
        try:
            flavor_id = int(key)
            count = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValidationError(f'Enter {label} for each flavour as a whole number.')
        if count < 0:
            raise ValidationError(f'{label.capitalize()} cannot be negative.')
        cleaned[str(flavor_id)] = count
    ids = {int(k) for k in cleaned}
    missing = ids - set(Flavor.objects.filter(id__in=ids).values_list('id', flat=True))
    if missing:
        raise ValidationError(f'Unknown flavour id(s): {", ".join(str(i) for i in sorted(missing))}.')
    return cleaned


@transaction.atomic
def log_visit(user, company, purpose, outcome=None, notes='', pack_counts=None, follow_up_date=None):
    outcome = outcome or B2BActivity.VisitOutcome.NONE
    if purpose in B2BActivity.PACK_COUNT_PURPOSES:
        pack_counts = clean_flavor_counts(pack_counts, 'packs on the shelf')
    else:
        pack_counts = None
    label = _VISIT_PURPOSE_LABELS.get(purpose, purpose)
    subject = f'Field visit — {label}'
    if outcome != B2BActivity.VisitOutcome.NONE:
        subject += f' ({outcome.replace("_", " ")})'

    activity = B2BActivity.objects.create(
        company=company, admin_user=user, type=B2BActivity.Type.VISIT,
        visit_purpose=purpose, visit_outcome=outcome, pack_counts=pack_counts,
        subject=subject, description=notes,
        follow_up_date=follow_up_date if outcome == B2BActivity.VisitOutcome.FOLLOW_UP else None,
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


# ── Payment terms ──────────────────────────────────────────────────────────

def clean_fixed_dates(raw):
    """"1, 15" or [1, 15] -> sorted unique days of the month, 1..31."""
    if raw in (None, ''):
        return []
    parts = raw if isinstance(raw, (list, tuple)) else str(raw).replace(' ', '').split(',')
    days = set()
    for part in parts:
        if part in ('', None):
            continue
        try:
            day = int(part)
        except (TypeError, ValueError):
            raise ValidationError('Fixed dates must be days of the month, e.g. 1, 15.')
        if not 1 <= day <= 31:
            raise ValidationError('Fixed dates must be between 1 and 31.')
        days.add(day)
    return sorted(days)


def clean_payment_terms(body):
    """Validate the payment-terms fields of an add/edit company request.
    Returns a dict of model field values."""
    try:
        upfront = Decimal(str(body.get('upfront_percent') or 0))
    except InvalidOperation:
        raise ValidationError('Upfront share must be a percentage.')
    if not 0 <= upfront <= 100:
        raise ValidationError('Upfront share must be between 0% and 100%.')

    upfront_due = body.get('upfront_due') or B2BCompany.UpfrontDue.DELIVERY
    if upfront_due not in B2BCompany.UpfrontDue.values:
        raise ValidationError('Invalid upfront due option.')
    rule = body.get('balance_rule') or B2BCompany.BalanceRule.BILL_TO_BILL
    if rule not in B2BCompany.BalanceRule.values:
        raise ValidationError('Invalid balance rule.')

    raw_days = body.get('payment_terms_days')
    try:
        days = int(raw_days) if raw_days not in (None, '') else 30
    except (TypeError, ValueError):
        raise ValidationError('Days must be a whole number.')
    if days < 0:
        raise ValidationError('Days cannot be negative.')

    fixed = clean_fixed_dates(body.get('payment_fixed_dates'))
    if upfront < 100 and rule == B2BCompany.BalanceRule.FIXED_DATES and not fixed:
        raise ValidationError('Enter the fixed dates the balance is due on, e.g. 1, 15.')

    return {
        'upfront_percent': upfront,
        'upfront_due': upfront_due,
        'balance_rule': rule,
        'payment_terms_days': days,
        'payment_fixed_dates': fixed,
    }


def _next_fixed_date(after, days):
    """First date strictly after `after` that falls on one of `days` (a day
    past the month's end, e.g. 31 in June, means the month's last day)."""
    year, month = after.year, after.month
    for _ in range(3):
        last = calendar.monthrange(year, month)[1]
        for day in days:
            candidate = date(year, month, min(day, last))
            if candidate > after:
                return candidate
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return None


def payment_due_date(company, order_date=None):
    """Default due date for the balance of a new order under the company's
    terms. Bill to bill is really "due when the next bill is raised", which
    has no date yet — the N-day backstop stands in for it. Weekly as sold
    settles at the next weekly visit."""
    order_date = order_date or date.today()
    if company is None:
        return None
    if company.upfront_percent >= 100:
        return order_date
    rule = company.balance_rule
    if rule == B2BCompany.BalanceRule.WEEKLY_AS_SOLD:
        return order_date + timedelta(days=7)
    if rule == B2BCompany.BalanceRule.FIXED_DATES and company.payment_fixed_dates:
        return _next_fixed_date(order_date, company.payment_fixed_dates)
    if company.payment_terms_days > 0:
        return order_date + timedelta(days=company.payment_terms_days)
    return order_date


def payment_terms_label(company):
    """Short human summary, e.g. "50% with the order, balance after 30 days"."""
    upfront = company.upfront_percent.normalize() if company.upfront_percent else Decimal(0)
    upfront_txt = f'{upfront:f}%'
    when = 'with the order' if company.upfront_due == B2BCompany.UpfrontDue.ADVANCE else 'on delivery'
    if upfront >= 100:
        return f'100% {when}'
    rule = company.balance_rule
    days = company.payment_terms_days
    if rule == B2BCompany.BalanceRule.BILL_TO_BILL:
        balance = f'bill to bill (max {days} days)'
    elif rule == B2BCompany.BalanceRule.WEEKLY_AS_SOLD:
        balance = 'weekly as sold'
    elif rule == B2BCompany.BalanceRule.FIXED_DATES:
        balance = 'on the ' + ', '.join(str(d) for d in company.payment_fixed_dates) + ' of the month'
    else:
        balance = f'after {days} days'
    if upfront <= 0:
        return f'Balance {balance}'
    return f'{upfront_txt} {when}, balance {balance}'
