"""
Celery tasks for the Content Studio (content-generator-plan.md §8, §13
Phase 1/2/3). Same idiom as debugger/tasks.py: set a transient status before
dispatch, catch SoftTimeLimitExceeded/Exception, always land back on a
stable status with the error surfaced on the row.

run_copywriter_daily/run_designer_daily are thin beat-schedule wrappers around
content.agents.copywriter.run_daily()/content.agents.designer.run_daily()
respectively. Unlike run_planner_task, these sweep ALL due PlanItems across
brands in one call rather than acting on a single row, so there's no
per-object status to flip before/after — each agent's own per-item loop
already records a failure on that item's Script/PosterAsset rather than
raising, so the only thing these wrappers guard against is the whole sweep
dying outright (SoftTimeLimitExceeded, or an exception before the loop starts).
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

    # No PLAN_REVIEW ActionItem any more (§27) — approval is item-wise, not
    # plan-wise; each new item sits as PLANNED on the Calendar page, which
    # already surfaces "N pending" per plan without a separate task needed.

    logger.info('run_planner_task: plan %s generated %s item(s)', plan_id, created)
    return {'plan_id': plan_id, 'created': created}


@shared_task(bind=True, max_retries=0)
def run_planner_regenerate_task(self, plan_id, instruction=''):
    """Plan-level "Regenerate" trigger — content/views.py's
    RegeneratePlanAPI. Same status-transition contract as run_planner_task
    (DRAFT + generation_error on failure, NEEDS_REVIEW on success), just
    calling planner.regenerate_plan instead of generate_plan."""
    from content.agents import planner
    from content.models import ContentPlan

    plan = ContentPlan.objects.filter(id=plan_id).first()
    if not plan:
        logger.warning('run_planner_regenerate_task: plan %s not found', plan_id)
        return

    try:
        result = planner.regenerate_plan(plan, instruction=instruction)
    except SoftTimeLimitExceeded:
        logger.warning('run_planner_regenerate_task timed out for plan %s', plan_id)
        plan.status = ContentPlan.Status.DRAFT
        plan.generation_error = 'Timed out regenerating the plan. Try again, or narrow the period.'
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        return
    except Exception as exc:
        logger.exception('run_planner_regenerate_task failed for plan %s', plan_id)
        plan.status = ContentPlan.Status.DRAFT
        plan.generation_error = str(exc)[:2000]
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        return

    plan.status = ContentPlan.Status.NEEDS_REVIEW
    plan.generation_error = ''
    plan.save(update_fields=['status', 'generation_error', 'updated_at'])

    logger.info(
        'run_planner_regenerate_task: plan %s deleted %s, created %s item(s)',
        plan_id, result['deleted'], result['created'])
    return {'plan_id': plan_id, **result}


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


@shared_task(bind=True, max_retries=0)
def run_copywriter_daily(self):
    """Beat task (content-generator-plan.md §7/§13 Phase 2): sweep every
    active brand's due PlanItems, drafting Scripts where inputs are ready
    and filing input_needed ActionItems where they aren't. See
    content/agents/copywriter.py's module docstring for the full
    status-transition scheme this sweeps against."""
    from content.agents import copywriter

    try:
        summary = copywriter.run_daily()
    except SoftTimeLimitExceeded:
        logger.warning('run_copywriter_daily timed out')
        return
    except Exception:
        logger.exception('run_copywriter_daily failed')
        return
    logger.info('run_copywriter_daily: %s', summary)
    return summary


@shared_task(bind=True, max_retries=0)
def run_planner_nudge_daily(self):
    """Beat task (content-generator-plan.md §13 Phase 1 step 3 / Phase 4):
    sweep every APPROVED plan's still-future items for a diff-worthy change
    given newly flagged trends, writing any proposal to PlanItem.proposed_changes
    for admin review (accept/reject) rather than applying it directly. See
    content/agents/planner.py's run_daily_nudge() docstring — most days this
    makes zero LLM calls (no open TrendFlags to react to)."""
    from content.agents import planner

    try:
        summary = planner.run_daily_nudge()
    except SoftTimeLimitExceeded:
        logger.warning('run_planner_nudge_daily timed out')
        return
    except Exception:
        logger.exception('run_planner_nudge_daily failed')
        return
    logger.info('run_planner_nudge_daily: %s', summary)
    return summary


@shared_task(bind=True, max_retries=0)
def run_designer_daily(self):
    """Beat task (content-generator-plan.md §13 Phase 3): sweep every
    PlanItem with an approved Script and a poster content_type, generating
    a PosterAsset for each one not already attempted. See
    content/agents/designer.py's run_daily() docstring for the exact
    gating rule (a failed attempt counts as "already attempted" so a
    broken Gemini key can't retry-storm every run)."""
    from content.agents import designer

    try:
        summary = designer.run_daily()
    except SoftTimeLimitExceeded:
        logger.warning('run_designer_daily timed out')
        return
    except Exception:
        logger.exception('run_designer_daily failed')
        return
    logger.info('run_designer_daily: %s', summary)
    return summary
