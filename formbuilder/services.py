"""Business logic for the form builder: validation, submissions, users, results."""
import csv
import io
import os
import re
import secrets
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, F, Q
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from formbuilder import tokens
from formbuilder.models import HEX_COLOR_RE, SLUG_RE, Form, FormBranding, FormField, Submission

CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O/1/I
MIN_CODE_LENGTH, MAX_CODE_LENGTH = 4, 12
SHORT_MAX, LONG_MAX = 500, 5000
MAX_OPTIONS = 500
TEXT_TYPES = (FormField.TEXT, FormField.LONG_TEXT, FormField.EMAIL, FormField.PHONE)


class FormError(Exception):
    """A rule violation the admin should see, as a plain message."""


# ── field definition ────────────────────────────────────────────────────

def make_slug(form, label):
    base = slugify(label).replace('-', '_')[:30] or 'field'
    if not base[0].isalpha():
        base = f'f_{base}'
    slug, n = base, 2
    taken = set(form.fields.values_list('slug', flat=True))
    while slug in taken:
        slug = f'{base}_{n}'
        n += 1
    return slug


def clean_options(raw):
    if isinstance(raw, str):
        raw = raw.splitlines()
    seen, out = set(), []
    for item in raw or []:
        item = str(item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item[:200])
    if len(out) > MAX_OPTIONS:
        raise FormError(f'A dropdown can have at most {MAX_OPTIONS} options.')
    return out


def validate_field_definition(form, field):
    """Raise FormError if `field` (already populated) breaks a rule."""
    if not field.label.strip():
        raise FormError('Label is required.')
    if field.field_type == FormField.DROPDOWN:
        if not field.options:
            raise FormError('A dropdown needs at least one option.')
    else:
        field.options = []
    if field.role:
        needed = FormField.ROLE_TYPES.get(field.role)
        if needed is None:
            raise FormError('Unknown field role.')
        if field.field_type != needed:
            raise FormError(f'A {field.get_role_display().lower()} field must be of type "{needed}".')
        clash = form.fields.filter(role=field.role, is_archived=False).exclude(pk=field.pk)
        if clash.exists():
            raise FormError(f'This form already has a {field.get_role_display().lower()} field.')
    if form.key_field_id == field.pk and field.pk and field.field_type != FormField.DROPDOWN:
        raise FormError('The key field must stay a dropdown.')


def check_type_change(field, new_type):
    if new_type != field.field_type and field.form.submissions.exists():
        raise FormError('This form already has responses, so field types can no longer change.')


def set_key_field(form, field):
    """field=None clears the key."""
    if field is not None:
        if field.form_id != form.id or field.is_archived:
            raise FormError('Pick one of this form\'s active fields.')
        if field.field_type != FormField.DROPDOWN:
            raise FormError('Only a dropdown field can be the key field.')
    form.key_field = field
    form.save(update_fields=['key_field', 'updated_at'])


def remove_field(field):
    """Delete a field, or archive it if responses exist. Returns 'deleted' or 'archived'."""
    form = field.form
    if form.key_field_id == field.pk:
        form.key_field = None
        form.save(update_fields=['key_field', 'updated_at'])
    if form.submissions.exists():
        field.is_archived = True
        field.role = FormField.ROLE_NONE
        field.save(update_fields=['is_archived', 'role'])
        return 'archived'
    field.delete()
    return 'deleted'


# ── links ───────────────────────────────────────────────────────────────

def key_options(form):
    key = form.key_field
    return list(key.options) if key and not key.is_archived else []


def resolve_prefill(form, token):
    """
    Decode a link token into {slug: value}, or None when the token is absent or
    unusable (tampered, other form, key field changed, option since removed).
    The caller then simply serves the general form.
    """
    key = form.key_field
    if key is None or key.is_archived:
        return None
    data = tokens.read_token(form.public_id, token)
    if not data or set(data) != {key.slug}:
        return None
    value = data[key.slug]
    if not isinstance(value, str) or value not in key.options:
        return None
    return data


def build_links(form, base_url, only=None):
    general = tokens.form_url(form, base_url)
    key = form.key_field
    rows = []
    if key and not key.is_archived:
        for opt in key.options:
            if only is not None and opt not in only:
                continue
            rows.append({'value': opt, 'url': tokens.form_url(form, base_url, {key.slug: opt})})
    return {'general_url': general, 'key_label': key.label if key else None, 'links': rows}


# ── submission ──────────────────────────────────────────────────────────

def clean_value(field, raw):
    """Return (value, error). Empty optional input gives (None, None)."""
    t = field.field_type
    if t == FormField.CHECKBOX:
        checked = str(raw).strip().lower() in ('on', 'true', '1', 'yes') if raw is not None else False
        if field.required and not checked:
            return None, 'This must be ticked.'
        return checked, None

    text = '' if raw is None else str(raw).strip()
    if not text:
        return None, ('This field is required.' if field.required else None)

    if t in (FormField.TEXT, FormField.LONG_TEXT):
        limit = LONG_MAX if t == FormField.LONG_TEXT else SHORT_MAX
        if len(text) > limit:
            return None, f'Please keep this under {limit} characters.'
        return text, None
    if t == FormField.NUMBER:
        try:
            num = Decimal(text)
            if not num.is_finite() or abs(num) >= Decimal('1e15'):
                raise InvalidOperation
        except InvalidOperation:
            return None, 'Enter a valid number.'
        return (int(num) if num == num.to_integral_value() else float(num)), None
    if t == FormField.EMAIL:
        try:
            validate_email(text)
        except ValidationError:
            return None, 'Enter a valid email address.'
        return text.lower(), None
    if t == FormField.PHONE:
        from storefront.services import clean_mobile
        mobile = clean_mobile(text)
        if not mobile:
            return None, 'Enter a valid 10-digit mobile number.'
        return mobile, None
    if t == FormField.DROPDOWN:
        if text not in field.options:
            return None, 'Choose one of the listed options.'
        return text, None
    if t == FormField.DATE:
        try:
            return date.fromisoformat(text).isoformat(), None
        except ValueError:
            return None, 'Enter a valid date.'
    if t == FormField.RATING:
        if not re.fullmatch(r'[1-5]', text):
            return None, 'Choose a rating from 1 to 5.'
        return int(text), None
    return None, 'Unsupported field.'


def generate_code(form):
    length = min(max(form.code_length, MIN_CODE_LENGTH), MAX_CODE_LENGTH)
    for _ in range(30):
        code = form.code_prefix + ''.join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if not Submission.objects.filter(form=form, code=code).exists():
            return code
    raise FormError('Could not generate a unique code. Increase the code length.')


def _upsert_user(form, name, phone, email):
    if not (phone or email):
        return None
    brand_users = User.objects.filter(brand_id=form.brand_id)
    user = None
    if phone:
        user = brand_users.filter(mobile=phone).first()
    if user is None and email:
        user = brand_users.filter(email__iexact=email).first()
    if user is None:
        try:
            with transaction.atomic():
                return User.objects.create(
                    brand_id=form.brand_id, mobile=phone or None, email=email or '',
                    name=name or '', is_lead=True)
        except IntegrityError:           # created concurrently with the same mobile
            user = brand_users.filter(mobile=phone).first()
            if user is None:
                raise
    # Fill gaps only; never overwrite what the customer or an admin set.
    changed = []
    if name and not user.name:
        user.name = name
        changed.append('name')
    if email and not user.email:
        user.email = email
        changed.append('email')
    if phone and not user.mobile:
        user.mobile = phone
        changed.append('mobile')
    if changed:
        user.save(update_fields=changed + ['updated_at'])
    return user


def submit(form, posted, prefill=None):
    """
    Validate `posted` (a mapping of field slug -> raw value) and store it.
    Returns (submission, errors). `prefill` is the verified link payload; its
    values always win over anything posted for the same field.
    """
    prefill = prefill or {}
    answers, errors = {}, {}
    for field in form.fields.filter(is_archived=False):
        if field.slug in prefill:
            answers[field.slug] = prefill[field.slug]
            continue
        value, error = clean_value(field, posted.get(field.slug))
        if error:
            errors[field.slug] = error
        elif value is not None:
            answers[field.slug] = value
    if errors:
        return None, errors

    by_role = {f.role: answers.get(f.slug) for f in form.fields.filter(is_archived=False) if f.role}
    name = by_role.get(FormField.ROLE_NAME)
    phone = by_role.get(FormField.ROLE_PHONE)
    email = by_role.get(FormField.ROLE_EMAIL)

    with transaction.atomic():
        if form.one_response_per_phone and phone:
            Form.objects.select_for_update().get(pk=form.pk)    # serialise duplicate checks
            if Submission.objects.filter(form=form, phone=phone).exists():
                phone_field = form.fields.filter(role=FormField.ROLE_PHONE, is_archived=False).first()
                return None, {phone_field.slug: 'This number has already responded.'}
        user = _upsert_user(form, name, phone, email) if form.create_users else None
        submission = Submission.objects.create(
            form=form, answers=answers, via_link=bool(prefill), user=user, phone=phone or '',
            code=generate_code(form) if form.show_code else '')
    return submission, {}


# ── results: filter, search, export ─────────────────────────────────────

def _txt(slug):
    return KeyTextTransform(slug, 'answers')


def filter_submissions(form, params):
    """
    params: a QueryDict-like mapping.
      search            matches any text answer, or the code
      date_from/date_to submission date bounds (YYYY-MM-DD)
      f.<slug>          dropdown: comma-separated exact values; text/email/phone:
                        contains; checkbox: yes|no; rating: exact
      f.<slug>.min/.max numbers, ratings and dates
    """
    qs = form.submissions.all()
    fields = {f.slug: f for f in form.fields.all()}

    if params.get('date_from'):
        qs = qs.filter(created_at__date__gte=params['date_from'])
    if params.get('date_to'):
        qs = qs.filter(created_at__date__lte=params['date_to'])

    search = (params.get('search') or '').strip()
    if search:
        cond = Q(code__icontains=search)
        for slug, f in fields.items():
            if f.field_type in TEXT_TYPES or f.field_type == FormField.DROPDOWN:
                qs = qs.annotate(**{f'a_{slug}': _txt(slug)})
                cond |= Q(**{f'a_{slug}__icontains': search})
        qs = qs.filter(cond)

    for slug, f in fields.items():
        exact = (params.get(f'f.{slug}') or '').strip()
        lo = (params.get(f'f.{slug}.min') or '').strip()
        hi = (params.get(f'f.{slug}.max') or '').strip()
        if not (exact or lo or hi):
            continue
        qs = qs.annotate(**{f'a_{slug}': _txt(slug)})
        col = f'a_{slug}'
        if exact:
            if f.field_type == FormField.DROPDOWN:
                qs = qs.filter(**{f'{col}__in': [v.strip() for v in exact.split(',') if v.strip()]})
            elif f.field_type == FormField.CHECKBOX:
                qs = qs.filter(**{col: 'true' if exact.lower() in ('yes', 'true', '1') else 'false'})
            elif f.field_type in (FormField.RATING, FormField.NUMBER, FormField.DATE):
                qs = qs.filter(**{col: exact})
            else:
                qs = qs.filter(**{f'{col}__icontains': exact})
        if lo or hi:
            if f.field_type in (FormField.NUMBER, FormField.RATING):
                num = f'n_{slug}'
                qs = qs.annotate(**{num: Cast(col, DecimalField(max_digits=24, decimal_places=6))})
                try:
                    if lo:
                        qs = qs.filter(**{f'{num}__gte': Decimal(lo)})
                    if hi:
                        qs = qs.filter(**{f'{num}__lte': Decimal(hi)})
                except InvalidOperation:
                    pass
            elif f.field_type == FormField.DATE:
                if lo:
                    qs = qs.filter(**{f'{col}__gte': lo})
                if hi:
                    qs = qs.filter(**{f'{col}__lte': hi})
    return qs.select_related('user')


def display_value(value):
    if value is None:
        return ''
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    return str(value)


def csv_safe(text):
    """Stop spreadsheet apps treating a cell as a formula."""
    if text and text[0] in '=+-@\t\r':
        return "'" + text
    return text


def csv_columns(form):
    fields = list(form.fields.all())
    headers = ['Submitted at', 'Code', 'Via link'] + [
        f.label + (' (archived)' if f.is_archived else '') for f in fields]
    return fields, headers


def csv_rows(form, qs):
    """Generator of CSV text chunks, header first."""
    fields, headers = csv_columns(form)
    buf = io.StringIO()
    writer = csv.writer(buf)

    def flush():
        data = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return data

    writer.writerow(headers)
    yield flush()
    for sub in qs.iterator(chunk_size=500):
        row = [sub.created_at.isoformat(timespec='seconds'), sub.code, 'Yes' if sub.via_link else 'No']
        row += [csv_safe(display_value(sub.answers.get(f.slug))) for f in fields]
        row[1] = csv_safe(row[1])
        writer.writerow(row)
        yield flush()


# ── success-page download (lead magnet) ─────────────────────────────────

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024        # nginx client_max_body_size is 20M
MIN_DOWNLOAD_HOURS, MAX_DOWNLOAD_HOURS = 1, 24 * 90
DOWNLOAD_EXTENSIONS = {
    '.pdf', '.epub', '.zip', '.png', '.jpg', '.jpeg', '.webp', '.gif',
    '.docx', '.xlsx', '.pptx', '.csv', '.txt', '.md', '.mp3', '.mp4',
}
INLINE_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.txt', '.mp4', '.mp3'}
_DOWNLOAD_SALT = 'formbuilder-download-v1'


def clean_download_hours(raw):
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise FormError('Link validity must be a number of hours.')
    if not MIN_DOWNLOAD_HOURS <= n <= MAX_DOWNLOAD_HOURS:
        raise FormError(f'Link validity must be between {MIN_DOWNLOAD_HOURS} and {MAX_DOWNLOAD_HOURS} hours.')
    return n


def set_download(form, upload):
    """Attach `upload` (an UploadedFile) as the form's download, replacing any old one."""
    if upload is None:
        raise FormError('Choose a file to upload.')
    name = os.path.basename(upload.name or '')[:200]
    ext = os.path.splitext(name)[1].lower()
    if ext not in DOWNLOAD_EXTENSIONS:
        raise FormError('Allowed files: ' + ', '.join(sorted(e[1:] for e in DOWNLOAD_EXTENSIONS)) + '.')
    if upload.size > MAX_DOWNLOAD_BYTES:
        raise FormError('File is larger than 20 MB.')
    if upload.size == 0:
        raise FormError('File is empty.')
    old = form.download_file.name if form.download_file else ''
    form.download_file.save(name, upload, save=False)
    form.download_name = name
    form.save(update_fields=['download_file', 'download_name', 'updated_at'])
    if old and old != form.download_file.name:
        form.download_file.storage.delete(old)


def remove_download(form):
    if form.download_file:
        form.download_file.delete(save=False)
    form.download_file = ''
    form.download_name = ''
    form.save(update_fields=['download_file', 'download_name', 'updated_at'])


def download_token(form, submission):
    return signing.dumps({'f': form.public_id, 's': submission.pk}, salt=_DOWNLOAD_SALT, compress=False)


def read_download_token(form, token):
    """Return the Submission the token was issued for, or None if it is
    missing, tampered, expired, or belongs to another form."""
    if not token or len(token) > 300:
        return None
    try:
        data = signing.loads(token, salt=_DOWNLOAD_SALT, max_age=form.download_hours * 3600)
    except signing.BadSignature:                 # includes SignatureExpired
        return None
    if not isinstance(data, dict) or data.get('f') != form.public_id:
        return None
    return Submission.objects.filter(pk=data.get('s'), form=form).first()


def record_download(submission):
    Submission.objects.filter(pk=submission.pk).update(
        download_count=F('download_count') + 1, last_downloaded_at=timezone.now())


def download_is_inline(form):
    return os.path.splitext(form.download_name or form.download_file.name)[1].lower() in INLINE_EXTENSIONS


# ── per-brand branding of the public pages ──────────────────────────────

def clean_color(raw):
    value = (raw or '').strip()
    if value and not HEX_COLOR_RE.match(value):
        raise FormError('Colours must look like #1A2B3C.')
    return value.upper()


def branding_for(brand_id):
    return FormBranding.objects.filter(brand_id=brand_id).first()


def theme_css(branding):
    """CSS custom-property overrides for the public pages. Values are
    validated hex colours, so they are safe to drop into a <style> block."""
    if branding is None:
        return ''
    rules = []
    p, a, bg = branding.primary_color, branding.accent_color, branding.background_color
    if p:
        rules.append(
            f'.fx{{--leaf:{p};--leaf-deep:color-mix(in srgb,{p} 78%,#000);}}'
            f'.fx .fx-submit:not(.fx-submit--ghost){{background:var(--leaf-deep);box-shadow:0 5px 0 color-mix(in srgb,{p} 55%,#000),'
            f'0 14px 26px -12px color-mix(in srgb,{p} 60%,transparent);}}'
            f'.fx .fx-submit:not(.fx-submit--ghost):hover{{background:color-mix(in srgb,{p} 68%,#000);}}'
            f'.fx .fx-field.is-focus{{border-color:color-mix(in srgb,{p} 55%,transparent);'
            f'box-shadow:0 0 0 4px color-mix(in srgb,{p} 14%,transparent);}}'
            f'.fx input:focus,.fx select:focus,.fx textarea:focus{{box-shadow:0 0 0 3px color-mix(in srgb,{p} 22%,transparent);}}'
            f'.fx .fx-submit--ghost:hover{{background:color-mix(in srgb,{p} 12%,transparent);}}'
            f'.fx .fx-progress{{background:color-mix(in srgb,{p} 16%,transparent);}}'
            f'.fx .fx-meta span{{background:color-mix(in srgb,{p} 14%,transparent);}}'
            f'.fx .fx-wave{{color:color-mix(in srgb,{p} 45%,transparent);}}'
            f'.fx .fx-stage--quiet::before{{background:radial-gradient(closest-side,color-mix(in srgb,{p} 18%,transparent),transparent);}}'
            f'.fx .fx-hill--back{{fill:color-mix(in srgb,{p} 22%,#fff);}}'
            f'.fx .fx-hill--mid{{fill:color-mix(in srgb,{p} 38%,#fff);}}'
            f'.fx .fx-hill--front{{fill:color-mix(in srgb,{p} 55%,#fff);}}'
        )
    if a:
        rules.append(f'.fx{{--chip:{a};}}'
                     f'.fx .fx-progress-bar{{background:linear-gradient(90deg,var(--leaf),{a});}}'
                     f'.fx .fx-sun{{background:radial-gradient(closest-side,color-mix(in srgb,{a} 42%,transparent),transparent);}}')
    if bg:
        rules.append(f'.fx{{--cream:{bg};background:{bg};}}'
                     f'.fx .fx-scene{{background:linear-gradient(180deg,color-mix(in srgb,{bg} 70%,#fff) 0%,{bg} 62%);}}')
    return ''.join(rules)
