"""
Content Studio admin pages (see content-generator-plan.md).

Phase 0 (§15) built three read-only pages. Phase 1 (§13) adds the Planner
Agent's manual "Generate plan" trigger — PlannerTriggerAPI below — which
shares its Celery task (content.tasks.run_planner_task) with the monthly
cron seed (§8), so an admin-triggered run and the automated one behave
identically. §21 adds DesignerTestAPI: a one-off, synchronous "does the
poster pipeline actually work" check (no PosterAsset persisted, no
PlanItem required) — not the real Phase 3 review UI, just a way to
exercise generate_poster_brief/generate_poster_image end to end from the
browser instead of a shell script.
"""
import base64

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module
from core.models import Brand

from content.agents import designer
from content.models import ActionItem, ContentPlan, ContentSeries, PlanItem, PosterAsset, PosterFormat, Script
from content.tasks import run_planner_task


def _action_items_for_brand(brand_id):
    """ActionItem has no brand FK of its own — it reaches brand via
    `plan` (plan-level items) or `plan_item.plan` (everything else)."""
    return ActionItem.objects.filter(
        Q(plan__brand_id=brand_id) | Q(plan_item__plan__brand_id=brand_id),
    )


@admin_login_required
@require_module('content_dashboard')
@ensure_csrf_cookie
def content_dashboard_page(request):
    return render(request, 'admin/content-dashboard.html')


@admin_login_required
@require_module('content_calendar')
@ensure_csrf_cookie
def content_calendar_page(request):
    return render(request, 'admin/content-calendar.html')


@admin_login_required
@require_module('content_tasks')
@ensure_csrf_cookie
def pending_tasks_page(request):
    return render(request, 'admin/content-tasks.html')


def _month_bounds(today):
    start = today.replace(day=1)
    if start.month == 12:
        next_start = start.replace(year=start.year + 1, month=1)
    else:
        next_start = start.replace(month=start.month + 1)
    return start, next_start


def _action_item_dict(a):
    return {
        'id': a.id,
        'kind': a.kind,
        'title': a.title,
        'description': a.description,
        'due_date': a.due_date.isoformat() if a.due_date else None,
        'plan_item_id': a.plan_item_id,
        'created_at': a.created_at.isoformat(),
    }


class ContentDashboardAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'content_dashboard'

    def get(self, request):
        brand_id = current_brand_id(request)
        today = timezone.localdate()
        month_start, month_end = _month_bounds(today)

        items_this_month = PlanItem.objects.filter(
            plan__brand_id=brand_id,
            planned_date__gte=month_start, planned_date__lt=month_end,
        )
        planned_this_month = items_this_month.exclude(status=PlanItem.Status.SKIPPED).count()
        posted_this_month = items_this_month.filter(status=PlanItem.Status.POSTED).count()

        open_items = _action_items_for_brand(brand_id).filter(status=ActionItem.Status.OPEN)
        awaiting_input = open_items.filter(kind=ActionItem.Kind.INPUT_NEEDED).count()
        awaiting_review = open_items.exclude(kind=ActionItem.Kind.INPUT_NEEDED).count()

        pending_tasks = [
            _action_item_dict(a) for a in open_items.select_related('plan_item')[:6]
        ]

        recent_scripts = list(
            Script.objects.filter(plan_item__plan__brand_id=brand_id)
            .select_related('plan_item').order_by('-created_at')[:5]
            .values('id', 'skill_used', 'status', 'updated_at', 'plan_item__working_title'),
        )
        recent_posters = list(
            PosterAsset.objects.filter(plan_item__plan__brand_id=brand_id)
            .select_related('plan_item').order_by('-created_at')[:5]
            .values('id', 'status', 'updated_at', 'plan_item__working_title'),
        )

        return ok({
            'stats': {
                'planned_this_month': planned_this_month,
                'awaiting_input': awaiting_input,
                'awaiting_review': awaiting_review,
                'posted_this_month': posted_this_month,
            },
            'pending_tasks': pending_tasks,
            'recent_scripts': recent_scripts,
            'recent_posters': recent_posters,
            'has_active_plan': ContentPlan.objects.filter(
                brand_id=brand_id, period_end__gte=today, status=ContentPlan.Status.APPROVED,
            ).exists(),
        })


class ContentCalendarAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'content_calendar'

    def get(self, request):
        brand_id = current_brand_id(request)
        qs = PlanItem.objects.filter(
            plan__brand_id=brand_id,
        ).select_related('series', 'plan').order_by('planned_date')
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if start:
            qs = qs.filter(planned_date__gte=start)
        if end:
            qs = qs.filter(planned_date__lte=end)
        items = [
            {
                'id': p.id,
                'plan_id': p.plan_id,
                'planned_date': p.planned_date.isoformat(),
                'working_title': p.working_title,
                'context_notes': p.context_notes,
                'status': p.status,
                'content_type': p.content_type,
                'series': p.series.name if p.series else None,
                'series_slug': p.series.slug if p.series else None,
                'plan_status': p.plan.status,
            }
            for p in qs
        ]

        plans_qs = ContentPlan.objects.filter(brand_id=brand_id)
        if start:
            plans_qs = plans_qs.filter(period_end__gte=start)
        if end:
            plans_qs = plans_qs.filter(period_start__lte=end)
        plans_qs = plans_qs.annotate(
            total_count=Count('items'),
            approved_count=Count('items', filter=Q(items__status=PlanItem.Status.APPROVED)),
            skipped_count=Count('items', filter=Q(items__status=PlanItem.Status.SKIPPED)),
        )
        plans = [
            {
                'id': pl.id,
                'period_start': pl.period_start.isoformat(),
                'period_end': pl.period_end.isoformat(),
                'status': pl.status,
                'generation_error': pl.generation_error,
                'total_count': pl.total_count,
                'approved_count': pl.approved_count,
                'skipped_count': pl.skipped_count,
                'pending_count': pl.total_count - pl.approved_count - pl.skipped_count,
            }
            for pl in plans_qs.order_by('-period_start')
        ]

        return ok({'items': items, 'plans': plans})

    def post(self, request):
        """action=approve_plan|toggle_item_approve|toggle_item_skip|update_item|
        regenerate_item, same {success,message} action-dispatch style as
        marketing's ReviewsAPI.post — the admin-approval gate
        content-generator-plan.md §4 requires before a plan (or any item
        in it) moves forward.

        Per-item review is a 3-way toggle: PLANNED (undecided, the default)
        <-> APPROVED <-> SKIPPED, each a single click, clicking the active
        one again returns to PLANNED. "Approve Plan" is the bulk finish
        action — it approves whatever's still PLANNED (anything already
        individually approved or skipped is left as the admin set it) and
        locks the plan as a whole. update_item edits an item's own fields
        (title/date/content_type/context_notes) and is independent of its
        approve/skip status. regenerate_item asks the Planner for a fresh
        working_title/context_notes for the same slot (optionally steered
        by an `instruction` string) and — unlike update_item — resets
        status back to PLANNED, since the admin hasn't seen the new content
        yet. All item-level actions reject once the parent plan is
        APPROVED — a locked plan's items don't change.
        """
        brand_id = current_brand_id(request)
        action = request.data.get('action')

        if action == 'approve_plan':
            try:
                plan = ContentPlan.objects.get(id=int(request.data.get('plan_id') or 0), brand_id=brand_id)
            except (ContentPlan.DoesNotExist, TypeError, ValueError):
                return err('Plan not found.', status=404)
            if plan.status == ContentPlan.Status.APPROVED:
                return err('This plan is already approved.')
            if plan.status != ContentPlan.Status.NEEDS_REVIEW:
                return err(f'Plan is not ready for approval (status: {plan.status}).')
            plan.items.filter(status=PlanItem.Status.PLANNED).update(status=PlanItem.Status.APPROVED)
            plan.status = ContentPlan.Status.APPROVED
            plan.approved_by = request.user
            plan.approved_at = timezone.now()
            plan.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
            ActionItem.objects.filter(
                plan=plan, kind=ActionItem.Kind.PLAN_REVIEW, status=ActionItem.Status.OPEN,
            ).update(status=ActionItem.Status.DONE, resolved_by=request.user, resolved_at=timezone.now())
            return ok(message='Plan approved.')

        if action in ('toggle_item_approve', 'toggle_item_skip', 'update_item', 'regenerate_item'):
            try:
                item = PlanItem.objects.select_related('plan').get(
                    id=int(request.data.get('item_id') or 0), plan__brand_id=brand_id)
            except (PlanItem.DoesNotExist, TypeError, ValueError):
                return err('Plan item not found.', status=404)
            if item.plan.status == ContentPlan.Status.APPROVED:
                return err('This plan is already approved — items can no longer be changed.')

            if action == 'update_item':
                return self._update_item(item, request.data)
            if action == 'regenerate_item':
                return self._regenerate_item(item, request.data)

            target = PlanItem.Status.APPROVED if action == 'toggle_item_approve' else PlanItem.Status.SKIPPED
            item.status = PlanItem.Status.PLANNED if item.status == target else target
            item.save(update_fields=['status', 'updated_at'])
            return ok({'status': item.status})

        return err('Unknown action.')

    def _regenerate_item(self, item, data):
        """Re-runs the Planner for ONE existing item (same slot, fresh
        working_title/context_notes) — content-generator-plan.md §23."""
        from content.agents import planner

        instruction = (data.get('instruction') or '').strip()
        try:
            planner.regenerate_item(item, instruction=instruction)
        except Exception as exc:
            return err(f'Regeneration failed: {exc}')
        return ok({
            'working_title': item.working_title,
            'context_notes': item.context_notes,
            'status': item.status,
        }, message='Item regenerated — review the new content.')

    def _update_item(self, item, data):
        """Edit a PlanItem's content before approval — title, date, content
        type, and context notes. Deliberately does NOT touch item.status:
        editing and approve/skip are independent actions, same "each
        control does one thing" rule as the toggle buttons."""
        fields = []

        if 'working_title' in data:
            working_title = (data.get('working_title') or '').strip()
            if not working_title:
                return err('working_title cannot be empty.')
            item.working_title = working_title[:255]
            fields.append('working_title')

        if 'planned_date' in data:
            planned_date = parse_date(data.get('planned_date') or '')
            if not planned_date:
                return err('planned_date must be a valid date (YYYY-MM-DD).')
            item.planned_date = planned_date
            fields.append('planned_date')

        if 'content_type' in data:
            content_type = data.get('content_type')
            valid_types = [
                c for c in ContentSeries.ContentType.values
                if c != ContentSeries.ContentType.REEL_VERDICT
            ]
            if content_type not in valid_types:
                return err(f'content_type must be one of {valid_types}.')
            item.content_type = content_type
            fields.append('content_type')

        if 'context_notes' in data:
            item.context_notes = data.get('context_notes') or ''
            fields.append('context_notes')

        if not fields:
            return err('No fields to update.')

        item.save(update_fields=fields + ['updated_at'])
        return ok({
            'id': item.id,
            'working_title': item.working_title,
            'planned_date': item.planned_date.isoformat(),
            'content_type': item.content_type,
            'context_notes': item.context_notes,
        }, message='Item updated.')


class PlannerTriggerAPI(APIView):
    """Manual "Generate plan for [period]" trigger (content-generator-plan.md
    §8). Shares content.tasks.run_planner_task with the monthly cron seed —
    an admin-triggered run behaves identically to the automated one."""
    permission_classes = [HasModulePermission]
    permission_module = 'content_calendar'

    def post(self, request):
        brand_id = current_brand_id(request)
        period_start = parse_date(request.data.get('period_start') or '')
        period_end = parse_date(request.data.get('period_end') or '')
        if not period_start or not period_end:
            return err('period_start and period_end are required (YYYY-MM-DD).')
        if period_end < period_start:
            return err('period_end must not be before period_start.')

        plan, _created = ContentPlan.objects.get_or_create(
            brand_id=brand_id, period_start=period_start, period_end=period_end,
            defaults={'status': ContentPlan.Status.DRAFT},
        )
        if plan.status == ContentPlan.Status.GENERATING:
            return err('A plan is already being generated for this period.',
                       data={'plan_id': plan.id})
        if plan.status == ContentPlan.Status.APPROVED:
            return err('This period is already approved — pick a different period to add more.',
                       data={'plan_id': plan.id})

        plan.status = ContentPlan.Status.GENERATING
        plan.generation_error = ''
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        run_planner_task.delay(plan.id)
        return ok({'plan_id': plan.id, 'status': plan.status}, message='Generating plan…')


class DesignerTestAPI(APIView):
    """Synchronous end-to-end test of the Designer Agent's poster pipeline
    (select inspiration -> brief -> image), triggered manually from the
    admin dashboard. Persists nothing — no PosterAsset, no PlanItem — this
    is purely "does the pipeline produce an image right now", not the real
    review/approval flow (that's Phase 3). Runs synchronously (not via
    Celery) since it's a one-off manual click with an admin waiting on the
    result, same as generate_poster_brief's existing direct-call pattern;
    a real image call can take several seconds, which the frontend should
    show a loading state for, but is well within a normal request timeout.
    """
    permission_classes = [HasModulePermission]
    permission_module = 'content_dashboard'

    def post(self, request):
        brand_id = current_brand_id(request)
        brand = Brand.objects.filter(id=brand_id).first()
        if not brand:
            return err('No active brand selected.')

        topic = (request.data.get('topic') or '').strip()
        if not topic:
            return err('topic is required.')
        format = request.data.get('format') or PosterFormat.SQUARE
        if format not in PosterFormat.values:
            return err(f'format must be one of {list(PosterFormat.values)}.')
        context_notes = (request.data.get('context_notes') or '').strip()

        inspiration = designer.select_poster_inspiration(brand, topic, format=format)

        try:
            brief = designer.generate_poster_brief(brand, topic, inspiration, context_notes=context_notes)
        except Exception as exc:
            return err(f'Brief generation failed: {exc}')

        image_bytes, debug = designer.generate_poster_image(
            brief, inspiration, format=format, brand_name=brand.name,
        )

        return ok({
            'inspiration_id': inspiration.id if inspiration else None,
            'brief': brief,
            'debug': debug,
            'image_base64': base64.b64encode(image_bytes).decode('ascii') if image_bytes else None,
        })


class PendingTasksAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'content_tasks'

    def get(self, request):
        qs = _action_items_for_brand(current_brand_id(request)).filter(
            status=ActionItem.Status.OPEN,
        ).select_related('plan_item')
        kind = request.query_params.get('kind')
        if kind:
            qs = qs.filter(kind=kind)
        return ok({'items': [_action_item_dict(a) for a in qs]})
