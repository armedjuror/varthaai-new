"""Admin pages and APIs for the form builder (permission module: 'forms')."""
import json

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from billing.pdf import pdf_guard
from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from formbuilder import pdf, services
from formbuilder.models import Form, FormField
from formbuilder.services import FormError

PER_PAGE_CHOICES = (10, 20, 50, 100)


def _form_or_404(request, pk):
    form = Form.objects.filter(pk=pk, brand_id=current_brand_id(request)).select_related('key_field').first()
    if form is None:
        raise Http404
    return form


def _base_url(request):
    return (getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')).rstrip('/')


def _field_json(f):
    return {
        'id': f.id, 'label': f.label, 'slug': f.slug, 'field_type': f.field_type,
        'type_label': f.get_field_type_display(), 'required': f.required,
        'help_text': f.help_text, 'placeholder': f.placeholder, 'options': f.options,
        'role': f.role, 'position': f.position, 'is_archived': f.is_archived,
    }


def _form_json(form, with_fields=False):
    data = {
        'id': form.id, 'public_id': form.public_id, 'title': form.title,
        'description': form.description, 'status': form.status,
        'key_field_id': form.key_field_id,
        'create_users': form.create_users, 'one_response_per_phone': form.one_response_per_phone,
        'consent_text': form.consent_text,
        'success_title': form.success_title, 'success_message': form.success_message,
        'success_redirect_url': form.success_redirect_url, 'show_code': form.show_code,
        'code_length': form.code_length, 'code_prefix': form.code_prefix,
        'code_label': form.code_label, 'created_at': form.created_at.isoformat(),
    }
    if with_fields:
        data['fields'] = [_field_json(f) for f in form.fields.all()]
    return data


# ── pages ───────────────────────────────────────────────────────────────

@admin_login_required
@require_module('forms')
@ensure_csrf_cookie
def forms_page(request):
    return render(request, 'admin/forms.html')


@admin_login_required
@require_module('forms')
@ensure_csrf_cookie
def form_detail_page(request, pk):
    form = _form_or_404(request, pk)
    return render(request, 'admin/form-detail.html', {
        'form_id': form.id, 'form_title': form.title,
        'field_types': FormField.TYPE_CHOICES,
    })


# ── APIs ────────────────────────────────────────────────────────────────

def _apply_settings(form, d):
    """Copy the settings payload onto `form`, validating as we go."""
    if 'title' in d:
        title = (d['title'] or '').strip()
        if not title:
            raise FormError('Title is required.')
        form.title = title[:200]
    if 'description' in d:
        form.description = (d['description'] or '').strip()
    if 'status' in d:
        if d['status'] not in (Form.DRAFT, Form.LIVE, Form.CLOSED):
            raise FormError('Unknown status.')
        form.status = d['status']
    for flag in ('create_users', 'one_response_per_phone', 'show_code'):
        if flag in d:
            setattr(form, flag, bool(d[flag]))
    for text, limit in (('consent_text', 500), ('success_title', 200), ('code_label', 100), ('code_prefix', 10)):
        if text in d:
            setattr(form, text, (d[text] or '').strip()[:limit])
    if 'success_message' in d:
        form.success_message = (d['success_message'] or '').strip()[:5000]
    if 'success_redirect_url' in d:
        url = (d['success_redirect_url'] or '').strip()
        if url and not url.lower().startswith(('http://', 'https://')):
            raise FormError('Redirect URL must start with http:// or https://')
        form.success_redirect_url = url
    if 'code_length' in d:
        try:
            n = int(d['code_length'])
        except (TypeError, ValueError):
            raise FormError('Code length must be a number.')
        if not services.MIN_CODE_LENGTH <= n <= services.MAX_CODE_LENGTH:
            raise FormError(f'Code length must be between {services.MIN_CODE_LENGTH} and {services.MAX_CODE_LENGTH}.')
        form.code_length = n
    if form.code_prefix and not form.code_prefix.replace('-', '').isalnum():
        raise FormError('Code prefix can only use letters, digits and hyphens.')
    if form.show_code and len(form.code_prefix) + form.code_length > 30:
        raise FormError('Code prefix plus length is too long.')


class FormsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'forms'

    def get(self, request):
        qs = (Form.objects.filter(brand_id=current_brand_id(request))
              .annotate(response_count=Count('submissions')))
        rows = []
        for f in qs:
            row = _form_json(f)
            row['response_count'] = f.response_count
            rows.append(row)
        return ok({'forms': rows})

    def post(self, request):
        d = request.data
        action = d.get('action')
        brand_id = current_brand_id(request)
        try:
            if action == 'create':
                form = Form(brand_id=brand_id)
                _apply_settings(form, {'title': d.get('title'), 'description': d.get('description')})
                form.save()
                return ok({'id': form.id}, 'Form created.')
            if action == 'delete':
                form = _form_or_404(request, d.get('id'))
                form.delete()
                return ok(None, 'Form deleted.')
        except FormError as exc:
            return err(str(exc))
        return err('Unknown action.')


class FormDetailAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'forms'

    def get(self, request, pk):
        form = _form_or_404(request, pk)
        data = _form_json(form, with_fields=True)
        data['response_count'] = form.submissions.count()
        return ok(data)

    def post(self, request, pk):
        form = _form_or_404(request, pk)
        d = request.data
        action = d.get('action')
        try:
            if action == 'update_settings':
                _apply_settings(form, d)
                form.save()
                return ok(_form_json(form), 'Saved.')
            if action in ('add_field', 'add_user_field'):
                return self._add_field(form, d, quick=action == 'add_user_field')
            if action == 'update_field':
                return self._update_field(form, d)
            if action == 'remove_field':
                field = self._field(form, d.get('field_id'))
                result = services.remove_field(field)
                msg = ('Field removed.' if result == 'deleted'
                       else 'Field has responses, so it was archived. Existing data is kept.')
                return ok({'result': result}, msg)
            if action == 'reorder_fields':
                order = [int(i) for i in d.get('field_ids') or []]
                fields = {f.id: f for f in form.fields.all()}
                for pos, fid in enumerate(i for i in order if i in fields):
                    if fields[fid].position != pos:
                        fields[fid].position = pos
                        fields[fid].save(update_fields=['position'])
                return ok(None, 'Order saved.')
            if action == 'set_key_field':
                fid = d.get('field_id')
                services.set_key_field(form, self._field(form, fid) if fid else None)
                return ok(None, 'Key field updated.')
        except FormError as exc:
            return err(str(exc))
        except (TypeError, ValueError):
            return err('Invalid request.')
        return err('Unknown action.')

    @staticmethod
    def _field(form, field_id):
        field = form.fields.filter(pk=field_id).first()
        if field is None:
            raise FormError('Field not found.')
        return field

    def _add_field(self, form, d, quick):
        if quick:
            role = d.get('role')
            spec = {
                FormField.ROLE_NAME: ('Your name', FormField.TEXT),
                FormField.ROLE_PHONE: ('Phone number', FormField.PHONE),
                FormField.ROLE_EMAIL: ('Email', FormField.EMAIL),
            }.get(role)
            if spec is None:
                raise FormError('Unknown field role.')
            label, ftype = spec
            field = FormField(form=form, label=label, field_type=ftype, role=role)
        else:
            ftype = d.get('field_type')
            if ftype not in dict(FormField.TYPE_CHOICES):
                raise FormError('Pick a field type.')
            field = FormField(form=form, label=(d.get('label') or '').strip(), field_type=ftype,
                              role=d.get('role') or '')
        self._fill(field, d, quick)
        field.slug = services.make_slug(form, field.label)
        field.position = (form.fields.order_by('-position').values_list('position', flat=True).first() or 0) + 1
        services.validate_field_definition(form, field)
        field.save()
        return ok(_field_json(field), 'Field added.')

    def _update_field(self, form, d):
        field = self._field(form, d.get('field_id'))
        if field.is_archived:
            raise FormError('Archived fields cannot be edited.')
        new_type = d.get('field_type') or field.field_type
        if new_type not in dict(FormField.TYPE_CHOICES):
            raise FormError('Unknown field type.')
        services.check_type_change(field, new_type)
        field.field_type = new_type
        if 'role' in d:
            field.role = d.get('role') or ''
        self._fill(field, d, False)
        if form.key_field_id == field.id and new_type != FormField.DROPDOWN:
            raise FormError('The key field must stay a dropdown.')
        services.validate_field_definition(form, field)
        field.save()
        return ok(_field_json(field), 'Field saved.')

    @staticmethod
    def _fill(field, d, quick):
        if not quick and 'label' in d:
            field.label = (d.get('label') or '').strip()[:200]
        for key, limit in (('help_text', 300), ('placeholder', 100)):
            if key in d:
                setattr(field, key, (d.get(key) or '').strip()[:limit])
        if 'required' in d:
            field.required = bool(d['required'])
        if 'options' in d or field.field_type == FormField.DROPDOWN:
            field.options = services.clean_options(d.get('options', field.options))


class FormLinksAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'forms'

    def get(self, request, pk):
        form = _form_or_404(request, pk)
        return ok(services.build_links(form, _base_url(request)))


class FormResultsAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'forms'

    def get(self, request, pk):
        form = _form_or_404(request, pk)
        qs = services.filter_submissions(form, request.query_params)
        try:
            per_page = int(request.query_params.get('per_page') or 20)
            page_no = max(1, int(request.query_params.get('page') or 1))
        except ValueError:
            per_page, page_no = 20, 1
        if per_page not in PER_PAGE_CHOICES:
            per_page = 20
        page = Paginator(qs, per_page).get_page(page_no)
        rows = [{
            'id': s.id, 'created_at': s.created_at.isoformat(), 'code': s.code,
            'via_link': s.via_link, 'answers': s.answers,
            'user_id': s.user_id, 'user_name': s.user.name if s.user else '',
        } for s in page]
        return ok({
            'rows': rows, 'total': page.paginator.count, 'page': page.number,
            'per_page': per_page, 'pages': page.paginator.num_pages,
            'fields': [_field_json(f) for f in form.fields.all()],
        })


@admin_login_required
@require_module('forms')
def export_csv(request, pk):
    form = _form_or_404(request, pk)
    qs = services.filter_submissions(form, request.GET)
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    response = StreamingHttpResponse(services.csv_rows(form, qs), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="form-{form.public_id}-{stamp}.csv"'
    response['Cache-Control'] = 'no-store'
    return response


@admin_login_required
@require_module('forms')
@pdf_guard(admin=True)
def qr_pdf(request, pk):
    form = _form_or_404(request, pk)
    wanted = request.GET.get('options')
    only = None
    if wanted:
        try:
            only = set(json.loads(wanted))
        except ValueError:
            only = {v for v in wanted.split('|') if v}
    data = services.build_links(form, _base_url(request), only)
    if not data['links'] and not request.GET.get('general'):
        return HttpResponse('Nothing to print: this form has no key field options.', status=400)
    try:
        per_page = int(request.GET.get('per_page') or 6)
    except ValueError:
        per_page = 6
    content = pdf.qr_sheet_pdf(form, data['general_url'], data['links'], per_page,
                               include_general=bool(request.GET.get('general')))
    response = HttpResponse(content, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="form-{form.public_id}-qr.pdf"'
    return response
