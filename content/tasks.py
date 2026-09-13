"""
Celery tasks for the Content Studio (content-generator-plan.md §8, §13
Phase 1). Same idiom as debugger/tasks.py: set a transient status before
dispatch, catch SoftTimeLimitExceeded/Exception, always land back on a
stable status with the error surfaced on the row.
"""
import logging

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0)
def run_planner_task(self, plan_id):
    from content.agents import planner
    from content.models import ContentPlan

    plan = ContentPlan.objects.filter(id=plan_id).first()
    if not plan:
        logger.warning('run_planner_task: plan %s not found', plan_id)
        return

    try:
        created = planner.generate_plan(plan)
    except SoftTimeLimitExceeded:
        logger.warning('run_planner_task timed out for plan %s', plan_id)
        plan.status = ContentPlan.Status.DRAFT
        plan.generation_error = 'Timed out generating the plan. Try again, or narrow the period.'
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        return
    except Exception as exc:
        logger.exception('run_planner_task failed for plan %s', plan_id)
        plan.status = ContentPlan.Status.DRAFT
        plan.generation_error = str(exc)[:2000]
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        return

    plan.status = ContentPlan.Status.NEEDS_REVIEW
    plan.generation_error = ''
    plan.save(update_fields=['status', 'generation_error', 'updated_at'])

    if created:
        from content.models import ActionItem
        ActionItem.objects.get_or_create(
            plan=plan, kind=ActionItem.Kind.PLAN_REVIEW, status=ActionItem.Status.OPEN,
            defaults={
                'title': f'Review content plan: {plan.period_start} – {plan.period_end}',
                'description': f'The Planner Agent proposed {created} item(s) for this period.',
            },
        )

    logger.info('run_planner_task: plan %s generated %s item(s)', plan_id, created)
    return {'plan_id': plan_id, 'created': created}


def _next_month_bounds(today):
    from datetime import timedelta
    if today.month == 12:
        start = today.replace(year=today.year + 1, month=1, day=1)
    else:
        start = today.replace(month=today.month + 1, day=1)
    if start.month == 12:
        following = start.replace(year=start.year + 1, month=1, day=1)
    else:
        following = start.replace(month=start.month + 1, day=1)
    return start, following - timedelta(days=1)


@shared_task(bind=True, max_retries=0)
def seed_next_month_plans(self):
    """Beat task (28th of every month, see CELERY_BEAT_SCHEDULE): seed next
    month's ContentPlan for every active brand and kick off the Planner. Same
    run_planner_task the admin's manual "Generate plan" trigger uses — one
    code path either way (content-generator-plan.md §8). Never touches a
    plan an admin has already APPROVED, and skips one already GENERATING
    (e.g. a manual trigger raced this)."""
    from django.utils import timezone

    from content.models import ContentPlan
    from core.models import Brand

    period_start, period_end = _next_month_bounds(timezone.localdate())
    dispatched = 0
    for brand in Brand.objects.filter(is_active=True):
        plan, _created = ContentPlan.objects.get_or_create(
            brand=brand, period_start=period_start, period_end=period_end,
            defaults={'status': ContentPlan.Status.DRAFT},
        )
        if plan.status in (ContentPlan.Status.APPROVED, ContentPlan.Status.GENERATING):
            continue
        plan.status = ContentPlan.Status.GENERATING
        plan.save(update_fields=['status', 'updated_at'])
        run_planner_task.delay(plan.id)
        dispatched += 1
    logger.info('seed_next_month_plans: dispatched %s plan(s)', dispatched)
    return dispatched
