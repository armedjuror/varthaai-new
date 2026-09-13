"""
Content Studio admin pages (see content-generator-plan.md).

Phase 0 (§15) built three read-only pages. Phase 1 (§13) adds the Planner
Agent's manual "Generate plan" trigger — PlannerTriggerAPI below — which
shares its Celery task (content.tasks.run_planner_task) with the monthly
cron seed (§8), so an admin-triggered run and the automated one behave
identically.
"""
from django.db.models import Q
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module

from content.models import ActionItem, ContentPlan, PlanItem, PosterAsset, Script
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
                'planned_date': p.planned_date.isoformat(),
                'working_title': p.working_title,
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
        plans = [
            {
                'id': pl.id,
                'period_start': pl.period_start.isoformat(),
                'period_end': pl.period_end.isoformat(),
                'status': pl.status,
                'generation_error': pl.generation_error,
            }
            for pl in plans_qs.order_by('-period_start')
        ]

        return ok({'items': items, 'plans': plans})


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
