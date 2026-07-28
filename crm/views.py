"""
B2B CRM slice: the pipeline page, the company detail page and their API.

Mirrors the PHP `admin/api/b2b.php` (company / contact / activity / category /
pipeline actions only — B2B orders, payments, offers and the B2B dashboard are
served by the orders app). One APIView handles GET (`?view=`) and
POST-with-`action`, matching the PHP endpoint. Everything is scoped to the
active brand via `current_brand_id`.
"""
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from crm.models import B2BActivity, B2BCategory, B2BCompany, B2BContact

AdminUser = get_user_model()

VALID_STAGES = ['lead', 'contacted', 'negotiation', 'converted', 'lost']
VALID_ACTIVITY_TYPES = ['call', 'meeting', 'email', 'whatsapp', 'note']


# ── HTML pages ──────────────────────────────────────────────────────────────

@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def pipeline_page(request):
    return render(request, 'admin/b2b.html')


@admin_login_required
@require_module('b2b')
@ensure_csrf_cookie
def view_b2b_page(request, pk):
    return render(request, 'admin/view-b2b.html', {'company_id': pk})


# ── Serialisers (plain dicts; booleans as 1/0 for the ported jQuery) ─────────

def _company_row(c):
    return {
        'id': c.id,
        'company_name': c.company_name,
        'category_id': c.category_id,
        'category_name': c.category.name if c.category else None,
        'city': c.city,
        'stage': c.stage,
        'assigned_to': c.assigned_to_id,
        'assigned_name': c.assigned_to.name if c.assigned_to else None,
        'contact_count': getattr(c, 'contact_count', None),
    }


def _company_detail(c):
    return {
        'id': c.id,
        'company_name': c.company_name,
        'category_id': c.category_id,
        'category_name': c.category.name if c.category else None,
        'address': c.address,
        'city': c.city,
        'state': c.state,
        'pincode': c.pincode,
        'gst_number': c.gst_number,
        'stage': c.stage,
        'source': c.source,
        'assigned_to': c.assigned_to_id,
        'assigned_name': c.assigned_to.name if c.assigned_to else None,
        'discount_type': c.discount_type,
        'discount_value': float(c.discount_value) if c.discount_value is not None else None,
        'credit_limit': float(c.credit_limit),
        'payment_terms_days': c.payment_terms_days,
        'notes': c.notes,
        'converted_at': c.converted_at.isoformat() if c.converted_at else None,
        'created_at': c.created_at.isoformat(),
    }


def _contact_row(ct):
    return {
        'id': ct.id,
        'name': ct.name,
        'phone': ct.phone,
        'email': ct.email,
        'designation': ct.designation,
        'is_primary': 1 if ct.is_primary else 0,
        'is_active': 1 if ct.is_active else 0,
    }


def _activity_row(a):
    return {
        'id': a.id,
        'company_id': a.company_id,
        'type': a.type,
        'subject': a.subject,
        'description': a.description,
        'old_stage': a.old_stage,
        'new_stage': a.new_stage,
        'follow_up_date': a.follow_up_date.isoformat() if a.follow_up_date else None,
        'is_follow_up_done': 1 if a.is_follow_up_done else 0,
        'created_at': a.created_at.isoformat(),
        'admin_name': a.admin_user.name if a.admin_user else '',
        'contact_name': a.contact.name if a.contact else None,
    }


# ── API ─────────────────────────────────────────────────────────────────────

class B2BAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'b2b'

    # ── GET dispatch ──
    def get(self, request):
        brand_id = current_brand_id(request)
        view = request.query_params.get('view', 'companies')

        if view == 'categories':
            rows = B2BCategory.objects.filter(
                brand_id=brand_id, is_active=True,
            ).order_by('name')
            return ok([{'id': r.id, 'name': r.name} for r in rows])

        if view == 'admins':
            rows = AdminUser.objects.order_by('name')
            return ok([{'id': a.id, 'name': a.name} for a in rows])

        if view == 'pipeline_stats':
            return ok(self._pipeline_stats(brand_id))

        if view == 'follow_ups':
            return ok(self._follow_ups(brand_id))

        if view == 'company':
            return self._company_detail_response(request, brand_id)

        if view == 'activities':
            company_id = request.query_params.get('id') or request.query_params.get('company_id')
            acts = (
                B2BActivity.objects
                .filter(company_id=company_id, company__brand_id=brand_id)
                .select_related('admin_user', 'contact')
                .order_by('-created_at')[:50]
            )
            return ok([_activity_row(a) for a in acts])

        return self._companies_list(request, brand_id)

    def _pipeline_stats(self, brand_id):
        stats = {s: 0 for s in VALID_STAGES}
        stats['total'] = 0
        qs = B2BCompany.objects.filter(brand_id=brand_id, is_active=True)
        for row in qs.values('stage').annotate(cnt=Count('id')):
            if row['stage'] in stats:
                stats[row['stage']] = row['cnt']
            stats['total'] += row['cnt']
        return stats

    def _follow_ups(self, brand_id):
        acts = (
            B2BActivity.objects
            .filter(
                company__brand_id=brand_id,
                follow_up_date__isnull=False,
                is_follow_up_done=False,
            )
            .select_related('company')
            .order_by('follow_up_date')[:20]
        )
        return [
            {
                'id': a.id,
                'company_id': a.company_id,
                'company_name': a.company.company_name,
                'type': a.type,
                'subject': a.subject,
                'description': a.description,
                'follow_up_date': a.follow_up_date.isoformat() if a.follow_up_date else None,
            }
            for a in acts
        ]

    def _company_detail_response(self, request, brand_id):
        company = (
            B2BCompany.objects
            .filter(id=request.query_params.get('id'), brand_id=brand_id)
            .select_related('category', 'assigned_to')
            .first()
        )
        if not company:
            return err('Company not found.')
        contacts = company.contacts.order_by('-is_primary', 'name')
        activities = (
            company.activities
            .select_related('admin_user', 'contact')
            .order_by('-created_at')[:50]
        )
        return ok({
            'company': _company_detail(company),
            'contacts': [_contact_row(c) for c in contacts],
            'activities': [_activity_row(a) for a in activities],
        })

    def _companies_list(self, request, brand_id):
        stage = request.query_params.get('stage', '')
        search = (request.query_params.get('search') or '').strip()
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = min(100, max(10, int(request.query_params.get('per_page', 25))))
        except (TypeError, ValueError):
            per_page = 25

        qs = B2BCompany.objects.filter(brand_id=brand_id)
        if stage:
            qs = qs.filter(stage=stage)
        if search:
            qs = qs.filter(
                Q(company_name__icontains=search)
                | Q(city__icontains=search)
                | Q(gst_number__icontains=search)
            )

        total = qs.count()
        offset = (page - 1) * per_page
        rows = (
            qs.select_related('category', 'assigned_to')
            .annotate(contact_count=Count('contacts', filter=Q(contacts__is_active=True)))
            .order_by('-updated_at')[offset:offset + per_page]
        )
        return ok({
            'companies': [_company_row(c) for c in rows],
            'total': total,
            'page': page,
            'per_page': per_page,
        })

    # ── POST dispatch ──
    def post(self, request):
        brand_id = current_brand_id(request)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')

        handlers = {
            'add_company': self._add_company,
            'edit_company': self._edit_company,
            'change_stage': self._change_stage,
            'add_contact': self._add_contact,
            'edit_contact': self._edit_contact,
            'delete_contact': self._delete_contact,
            'add_activity': self._add_activity,
            'complete_follow_up': self._complete_follow_up,
            'add_category': self._add_category,
            'delete_category': self._delete_category,
        }
        handler = handlers.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request, brand_id, body)

    # ── company ──
    def _add_company(self, request, brand_id, body):
        name = (body.get('company_name') or '').strip()
        if not name:
            return err('Company name is required.')
        company = B2BCompany.objects.create(
            brand_id=brand_id,
            category_id=_int_or_none(body.get('category_id')),
            company_name=name,
            address=(body.get('address') or '').strip(),
            city=(body.get('city') or '').strip(),
            state=(body.get('state') or '').strip(),
            pincode=(body.get('pincode') or '').strip(),
            gst_number=(body.get('gst_number') or '').strip(),
            stage=body.get('stage') or 'lead',
            source=(body.get('source') or '').strip(),
            assigned_to_id=_int_or_none(body.get('assigned_to')),
            notes=(body.get('notes') or '').strip(),
        )
        contact_name = (body.get('contact_name') or '').strip()
        if contact_name:
            B2BContact.objects.create(
                company=company,
                name=contact_name,
                phone=(body.get('contact_phone') or '').strip(),
                email=(body.get('contact_email') or '').strip(),
                designation=(body.get('contact_designation') or '').strip(),
                is_primary=True,
            )
        return ok({'id': company.id}, 'Company added successfully!')

    def _edit_company(self, request, brand_id, body):
        company = B2BCompany.objects.filter(
            id=_int_or_none(body.get('id')), brand_id=brand_id,
        ).first()
        if not company:
            return err('Invalid company ID.')
        name = (body.get('company_name') or '').strip()
        if not name:
            return err('Company name is required.')
        company.category_id = _int_or_none(body.get('category_id'))
        company.company_name = name
        company.address = (body.get('address') or '').strip()
        company.city = (body.get('city') or '').strip()
        company.state = (body.get('state') or '').strip()
        company.pincode = (body.get('pincode') or '').strip()
        company.gst_number = (body.get('gst_number') or '').strip()
        company.source = (body.get('source') or '').strip()
        company.assigned_to_id = _int_or_none(body.get('assigned_to'))
        company.discount_type = body.get('discount_type') or ''
        company.discount_value = _decimal_or_none(body.get('discount_value'))
        company.credit_limit = _decimal_or_none(body.get('credit_limit')) or 0
        company.payment_terms_days = _int_or_none(body.get('payment_terms_days')) or 0
        company.notes = (body.get('notes') or '').strip()
        company.save()
        return ok(None, 'Company updated successfully!')

    def _change_stage(self, request, brand_id, body):
        company = B2BCompany.objects.filter(
            id=_int_or_none(body.get('id')), brand_id=brand_id,
        ).first()
        new_stage = body.get('stage') or ''
        if not company:
            return err('Company not found.')
        if new_stage not in VALID_STAGES:
            return err('Invalid stage.')
        old_stage = company.stage
        if old_stage == new_stage:
            return ok(None, 'No change.')
        company.stage = new_stage
        if new_stage == 'converted':
            company.converted_at = timezone.now()
        company.save()
        B2BActivity.objects.create(
            company=company,
            admin_user=request.user,
            type=B2BActivity.Type.STAGE_CHANGE,
            subject=f'Stage changed from {old_stage} to {new_stage}',
            old_stage=old_stage,
            new_stage=new_stage,
        )
        return ok(None, f'Stage updated to {new_stage}.')

    # ── contacts ──
    def _add_contact(self, request, brand_id, body):
        company = B2BCompany.objects.filter(
            id=_int_or_none(body.get('company_id')), brand_id=brand_id,
        ).first()
        name = (body.get('name') or '').strip()
        if not company or not name:
            return err('Company ID and name are required.')
        is_primary = bool(_int_or_none(body.get('is_primary')))
        if is_primary:
            company.contacts.update(is_primary=False)
        contact = B2BContact.objects.create(
            company=company,
            name=name,
            phone=(body.get('phone') or '').strip(),
            email=(body.get('email') or '').strip(),
            designation=(body.get('designation') or '').strip(),
            is_primary=is_primary,
        )
        return ok({'id': contact.id}, 'Contact added!')

    def _edit_contact(self, request, brand_id, body):
        contact = B2BContact.objects.filter(
            id=_int_or_none(body.get('id')), company__brand_id=brand_id,
        ).select_related('company').first()
        if not contact:
            return err('Invalid contact ID.')
        is_primary = bool(_int_or_none(body.get('is_primary')))
        if is_primary:
            contact.company.contacts.exclude(id=contact.id).update(is_primary=False)
        contact.name = (body.get('name') or '').strip()
        contact.phone = (body.get('phone') or '').strip()
        contact.email = (body.get('email') or '').strip()
        contact.designation = (body.get('designation') or '').strip()
        contact.is_primary = is_primary
        contact.is_active = bool(_int_or_none(body.get('is_active')) if body.get('is_active') is not None else 1)
        contact.save()
        return ok(None, 'Contact updated!')

    def _delete_contact(self, request, brand_id, body):
        contact = B2BContact.objects.filter(
            id=_int_or_none(body.get('id')), company__brand_id=brand_id,
        ).first()
        if not contact:
            return err('Error deleting contact.')
        contact.delete()
        return ok(None, 'Contact deleted.')

    # ── activities ──
    def _add_activity(self, request, brand_id, body):
        company = B2BCompany.objects.filter(
            id=_int_or_none(body.get('company_id')), brand_id=brand_id,
        ).first()
        act_type = body.get('type') or ''
        if not company or act_type not in VALID_ACTIVITY_TYPES:
            return err('Invalid activity type.')
        activity = B2BActivity.objects.create(
            company=company,
            contact_id=_int_or_none(body.get('contact_id')),
            admin_user=request.user,
            type=act_type,
            subject=(body.get('subject') or '').strip(),
            description=(body.get('description') or '').strip(),
            follow_up_date=body.get('follow_up_date') or None,
        )
        company.save(update_fields=['updated_at'])
        return ok({'id': activity.id}, 'Activity logged!')

    def _complete_follow_up(self, request, brand_id, body):
        activity = B2BActivity.objects.filter(
            id=_int_or_none(body.get('id')), company__brand_id=brand_id,
        ).first()
        if not activity:
            return err('Error updating follow-up.')
        activity.is_follow_up_done = True
        activity.save(update_fields=['is_follow_up_done'])
        return ok(None, 'Follow-up marked as done.')

    # ── categories ──
    def _add_category(self, request, brand_id, body):
        name = (body.get('name') or '').strip()
        if not name:
            return err('Category name required.')
        category = B2BCategory.objects.create(brand_id=brand_id, name=name)
        return ok({'id': category.id}, 'Category added!')

    def _delete_category(self, request, brand_id, body):
        category = B2BCategory.objects.filter(
            id=_int_or_none(body.get('id')), brand_id=brand_id,
        ).first()
        if not category:
            return err('Error deleting category.')
        category.is_active = False
        category.save(update_fields=['is_active'])
        return ok(None, 'Category deleted.')


# ── helpers ──────────────────────────────────────────────────────────────────

def _int_or_none(value):
    try:
        return int(value) if value not in (None, '', 'null') else None
    except (TypeError, ValueError):
        return None


def _decimal_or_none(value):
    try:
        return float(value) if value not in (None, '', 'null') else None
    except (TypeError, ValueError):
        return None
