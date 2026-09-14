"""
Varthaai Verdict admin surfaces (content-generator-plan.md §13 Phase 5):

- Intake form — the "richest ActionItem" (§5): product identity, ingredient/
  nutrition label photos (or typed text), Ajwad's Design/Pricing/Taste
  scores + notes. Multipart (file uploads), unlike every other content_type's
  plain-text context_notes. Saving is NOT gated on completeness — a partial
  save is fine, content.agents.copywriter._is_ready's VerdictIntake branch
  is what actually decides when an item is ready to draft (and the
  self-healing due_plan_items gate picks it up automatically once it is —
  no manual "draft now" trigger needed here).
- Verdict History — read-only table of past episodes. Deliberately reads
  Script.structured_json as the source of truth for product name/scores
  (not VerdictIntake) — a regenerated Script's numbers are what actually
  shipped, and VerdictIntake is just the input that produced it.

New files — content/views.py (owned by earlier phases) is untouched.
"""
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module

from content.models import ContentSeries, PlanItem, Script, VerdictIntake

_SCORE_FIELDS = ('design_score', 'pricing_score', 'taste_score')
_TEXT_FIELDS = (
    'product_name', 'product_category', 'market', 'price_point',
    'design_notes', 'pricing_notes', 'taste_notes',
    'ingredients_text', 'nutrition_text',
)
_FILE_FIELDS = ('product_photo', 'ingredients_label_photo', 'nutrition_label_photo')


@admin_login_required
@require_module('content_verdict')
@ensure_csrf_cookie
def verdict_intake_page(request, plan_item_id):
    return render(request, 'admin/verdict-intake.html', {'plan_item_id': plan_item_id})


@admin_login_required
@require_module('content_verdict')
@ensure_csrf_cookie
def verdict_history_page(request):
    return render(request, 'admin/verdict-history.html')


def _intake_dict(item, intake):
    d = {
        'plan_item_id': item.id,
        'working_title': item.working_title,
        'planned_date': item.planned_date.isoformat(),
        'plan_item_status': item.status,
    }
    for f in _TEXT_FIELDS + _SCORE_FIELDS:
        d[f] = getattr(intake, f, None) if intake else None
    for f in _FILE_FIELDS:
        image = getattr(intake, f, None) if intake else None
        d[f + '_url'] = image.url if image else None
    d['submitted_at'] = intake.submitted_at.isoformat() if intake and intake.submitted_at else None
    return d


class VerdictIntakeAPI(APIView):
    """
    GET  /admin/api/content/verdict/intake/<plan_item_id>/  -> current intake
         (or an empty shape if none exists yet).
    POST /admin/api/content/verdict/intake/<plan_item_id>/  -> multipart —
         create/update. File fields are only overwritten when a new file is
         actually included in the request; omitting one keeps whatever was
         previously uploaded (a partial re-save shouldn't wipe an existing photo).
    """
    permission_classes = [HasModulePermission]
    permission_module = 'content_verdict'
    parser_classes = [MultiPartParser, FormParser]

    def _get_item(self, request, plan_item_id):
        brand_id = current_brand_id(request)
        return get_object_or_404(
            PlanItem, id=plan_item_id, plan__brand_id=brand_id,
            content_type=ContentSeries.ContentType.REEL_VERDICT,
        )

    def get(self, request, plan_item_id):
        item = self._get_item(request, plan_item_id)
        intake = VerdictIntake.objects.filter(plan_item=item).first()
        return ok(_intake_dict(item, intake))

    def post(self, request, plan_item_id):
        item = self._get_item(request, plan_item_id)
        intake, _created = VerdictIntake.objects.get_or_create(plan_item=item)

        fields = []
        for f in _TEXT_FIELDS:
            if f in request.data:
                setattr(intake, f, (request.data.get(f) or '').strip())
                fields.append(f)

        for f in _SCORE_FIELDS:
            if f in request.data:
                raw = request.data.get(f)
                if raw in (None, ''):
                    setattr(intake, f, None)
                else:
                    try:
                        score = int(raw)
                    except (TypeError, ValueError):
                        return err(f'{f} must be a whole number.')
                    if not (1 <= score <= 10):
                        return err(f'{f} must be between 1 and 10.')
                    setattr(intake, f, score)
                fields.append(f)

        for f in _FILE_FIELDS:
            uploaded = request.FILES.get(f)
            if uploaded:
                setattr(intake, f, uploaded)
                fields.append(f)

        intake.submitted_by = request.user
        intake.submitted_at = timezone.now()
        fields += ['submitted_by', 'submitted_at', 'updated_at']
        intake.save(update_fields=list(dict.fromkeys(fields)))

        return ok(_intake_dict(item, intake), message='Verdict intake saved.')


def _score(structured, *path):
    d = structured or {}
    for key in path:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _history_row(item, script):
    structured = script.structured_json if script else None
    return {
        'plan_item_id': item.id,
        'planned_date': item.planned_date.isoformat(),
        'plan_item_status': item.status,
        'script_id': script.id if script else None,
        'script_status': script.status if script else None,
        'product_name': _score(structured, 'product') or '(not yet drafted)',
        'design_score': _score(structured, 'design', 'score'),
        'pricing_score': _score(structured, 'pricing', 'score'),
        'taste_score': _score(structured, 'taste', 'score'),
        'ingredients_score': _score(structured, 'ingredients_nutrition', 'score'),
        'final_score': _score(structured, 'final_score'),
        'legitimacy_status': _score(structured, 'ingredients_nutrition', 'legitimacy_status'),
        'controversial': bool(_score(structured, 'ingredients_nutrition', 'controversial')),
        'has_structured_data': bool(structured),
    }


class VerdictHistoryAPI(APIView):
    """One row per Varthaai Verdict PlanItem — its LATEST Script (any
    status; the admin can tell draft-vs-approved from script_status), so a
    regenerated episode shows its current numbers, not a stale earlier
    attempt. structured_json is the source of truth for every scored field
    (content-generator-plan.md §10) — VerdictIntake is the input, not the
    record."""
    permission_classes = [HasModulePermission]
    permission_module = 'content_verdict'

    def get(self, request):
        brand_id = current_brand_id(request)
        items = (
            PlanItem.objects.filter(
                plan__brand_id=brand_id, content_type=ContentSeries.ContentType.REEL_VERDICT,
            )
            .order_by('-planned_date')
        )
        # Latest-by-version Script per plan_item: order ascending by version
        # and let a later dict write win — avoids needing Postgres's
        # DISTINCT ON (which the ORM's plain .distinct('field') maps to and
        # requires the same field first in order_by, awkward to combine with
        # picking max version) for what's a small per-brand row count anyway.
        latest_scripts = {}
        for s in Script.objects.filter(plan_item__in=items).order_by('version'):
            latest_scripts[s.plan_item_id] = s

        rows = [_history_row(item, latest_scripts.get(item.id)) for item in items]
        return ok({'items': rows})
