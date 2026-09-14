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

from content import lifecycle
from content.agents import designer
from content.models import (
    ActionItem, BrandAsset, ContentPlan, ContentSeries, PlanItem, PosterAsset, PosterFormat, Script,
)
from content.tasks import run_planner_regenerate_task, run_planner_task


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
        # Resolved either from the plan-level FK (plan_review) or through
        # the item (everything else) — lets the Pending Tasks page deep-link
        # each kind to the right page without a second lookup.
        'plan_id': a.plan_id or (a.plan_item.plan_id if a.plan_item_id else None),
        # Lets Pending Tasks route an input_needed item for reel_verdict to
        # the Verdict intake form instead of the Content Calendar (Phase 5).
        'content_type': a.plan_item.content_type if a.plan_item_id else None,
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
            'recent_scripts': recent_scripts,
            'recent_posters': recent_posters,
            # No more plan-level APPROVED status to check (§27, item-wise
            # approval only) — "active" now just means a plan covering the
            # current period exists at all, regardless of status.
            'has_active_plan': ContentPlan.objects.filter(
                brand_id=brand_id, period_end__gte=today,
            ).exists(),
        })


class ContentCalendarAPI(APIView):
    permission_classes = [HasModulePermission]
    permission_module = 'content_calendar'

    def get(self, request):
        brand_id = current_brand_id(request)
        qs = PlanItem.objects.filter(
            plan__brand_id=brand_id,
        ).select_related('series', 'plan').prefetch_related('scripts', 'posters').order_by('planned_date')
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        if start:
            qs = qs.filter(planned_date__gte=start)
        if end:
            qs = qs.filter(planned_date__lte=end)
        items = []
        for p in qs:
            has_script = bool(p.scripts.all())
            has_approved_script = any(s.status == Script.Status.APPROVED for s in p.scripts.all())
            has_poster = bool(p.posters.all())
            has_approved_poster = any(ps.status == PosterAsset.Status.APPROVED for ps in p.posters.all())
            items.append({
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
                'proposed_changes': p.proposed_changes or {},
                # Drives the Calendar page's "Draft Script"/"Generate Poster"
                # manual triggers (§24) — only offered when nothing exists yet.
                'has_script': has_script,
                'has_approved_script': has_approved_script,
                'has_poster': has_poster,
                'has_approved_poster': has_approved_poster,
                # Derived 5-stage lifecycle (content/lifecycle.py) — None
                # until the item passes the approve/skip gate.
                'lifecycle_stage': lifecycle.lifecycle_stage(
                    p, has_script, has_approved_script, has_poster, has_approved_poster),
                'is_reel': p.content_type in lifecycle.REEL_CONTENT_TYPES,
                'manually_generated_at': p.manually_generated_at.isoformat() if p.manually_generated_at else None,
                'manually_approved_at': p.manually_approved_at.isoformat() if p.manually_approved_at else None,
                'published_at': p.published_at.isoformat() if p.published_at else None,
                # Informational only — see lifecycle.prep_due_date's docstring
                # for why this is NOT an "overdue" deadline for reaching Approved.
                'prep_due_date': lifecycle.prep_due_date(p).isoformat(),
            })

        plans_qs = ContentPlan.objects.filter(brand_id=brand_id)
        if start:
            plans_qs = plans_qs.filter(period_end__gte=start)
        if end:
            plans_qs = plans_qs.filter(period_start__lte=end)
        plans_qs = plans_qs.annotate(
            total_count=Count('items'),
            approved_count=Count('items', filter=Q(items__status=PlanItem.Status.APPROVED)),
            skipped_count=Count('items', filter=Q(items__status=PlanItem.Status.SKIPPED)),
            posted_count=Count('items', filter=Q(items__status=PlanItem.Status.POSTED)),
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
                'posted_count': pl.posted_count,
                'pending_count': pl.total_count - pl.approved_count - pl.skipped_count - pl.posted_count,
            }
            for pl in plans_qs.order_by('-period_start')
        ]

        return ok({'items': items, 'plans': plans})

    def post(self, request):
        """action=toggle_item_approve|toggle_item_skip|update_item|
        regenerate_item|accept_proposal|reject_proposal, same {success,message}
        action-dispatch style as marketing's ReviewsAPI.post.

        Item-wise approval only (§26/§27 — no plan-level gate; "approve_plan"
        was removed). Per-item review is a 3-way toggle: PLANNED (undecided,
        the default) <-> APPROVED <-> SKIPPED, each a single click, clicking
        the active one again returns to PLANNED. update_item edits an item's
        own fields (title/date/content_type/context_notes) and is
        independent of its approve/skip status. regenerate_item asks the
        Planner for a fresh working_title/context_notes for the same slot
        (optionally steered by an `instruction` string) and — unlike
        update_item — resets status back to PLANNED, since the admin hasn't
        seen the new content yet.

        accept_proposal/reject_proposal resolve a diff the Planner's daily
        nudge wrote into `proposed_changes` (content/agents/planner.py's
        run_daily_nudge, content-generator-plan.md §4's "sits as a diff
        awaiting separate approval, never silently overwrites"). accept
        applies the proposed fields onto the item and clears
        proposed_changes; reject just clears it, leaving the item exactly
        as it was.

        mark_generated/mark_approved/mark_published drive the derived
        5-stage lifecycle (content/lifecycle.py). mark_generated/
        mark_approved are reel-only manual milestones (toggle on/off);
        mark_published is the terminal stage for any content type, gated on
        the item having actually reached 'approved' first, and toggles
        PlanItem.status to/from POSTED.
        """
        brand_id = current_brand_id(request)
        action = request.data.get('action')

        if action in ('toggle_item_approve', 'toggle_item_skip', 'update_item', 'regenerate_item',
                      'accept_proposal', 'reject_proposal', 'mark_generated', 'mark_approved',
                      'mark_published'):
            try:
                item = PlanItem.objects.select_related('plan').get(
                    id=int(request.data.get('item_id') or 0), plan__brand_id=brand_id)
            except (PlanItem.DoesNotExist, TypeError, ValueError):
                return err('Plan item not found.', status=404)

            if action == 'accept_proposal':
                return self._accept_proposal(item, request.user)
            if action == 'reject_proposal':
                return self._reject_proposal(item, request.user)
            if action == 'update_item':
                return self._update_item(item, request.data)
            if action == 'regenerate_item':
                return self._regenerate_item(item, request.data)
            if action == 'mark_generated':
                return self._toggle_manual_generated(item, request.user)
            if action == 'mark_approved':
                return self._toggle_manual_approved(item, request.user)
            if action == 'mark_published':
                return self._toggle_published(item, request.user)

            if item.status == PlanItem.Status.POSTED:
                return err('This item has already been published — mark_published again to undo that first.')
            target = PlanItem.Status.APPROVED if action == 'toggle_item_approve' else PlanItem.Status.SKIPPED
            item.status = PlanItem.Status.PLANNED if item.status == target else target
            item.save(update_fields=['status', 'updated_at'])
            return ok({'status': item.status})

        return err('Unknown action.')

    def _toggle_manual_generated(self, item, user):
        if item.content_type not in lifecycle.REEL_CONTENT_TYPES:
            return err('Only reels use a manual "Generated" milestone — posters and blog reach it automatically.')
        if item.manually_generated_at:
            item.manually_generated_at = None
            item.manually_generated_by = None
            item.save(update_fields=['manually_generated_at', 'manually_generated_by', 'updated_at'])
            return ok({'manually_generated_at': None}, message='Generated milestone cleared.')
        item.manually_generated_at = timezone.now()
        item.manually_generated_by = user
        item.save(update_fields=['manually_generated_at', 'manually_generated_by', 'updated_at'])
        return ok({'manually_generated_at': item.manually_generated_at.isoformat()}, message='Marked as generated.')

    def _toggle_manual_approved(self, item, user):
        if item.content_type not in lifecycle.REEL_CONTENT_TYPES:
            return err('Only reels use a manual "Approved" milestone — posters and blog reach it via script/poster approval.')
        if item.manually_approved_at:
            item.manually_approved_at = None
            item.manually_approved_by = None
            item.save(update_fields=['manually_approved_at', 'manually_approved_by', 'updated_at'])
            return ok({'manually_approved_at': None}, message='Approved milestone cleared.')
        if not item.manually_generated_at:
            return err('Mark this reel as generated before approving it.')
        item.manually_approved_at = timezone.now()
        item.manually_approved_by = user
        item.save(update_fields=['manually_approved_at', 'manually_approved_by', 'updated_at'])
        return ok({'manually_approved_at': item.manually_approved_at.isoformat()}, message='Marked as approved.')

    def _toggle_published(self, item, user):
        if item.status == PlanItem.Status.POSTED:
            item.status = PlanItem.Status.APPROVED
            item.published_at = None
            item.published_by = None
            item.save(update_fields=['status', 'published_at', 'published_by', 'updated_at'])
            return ok({'status': item.status}, message='Published mark undone.')

        has_script = bool(item.scripts.all())
        has_approved_script = any(s.status == Script.Status.APPROVED for s in item.scripts.all())
        has_poster = bool(item.posters.all())
        has_approved_poster = any(ps.status == PosterAsset.Status.APPROVED for ps in item.posters.all())
        stage = lifecycle.lifecycle_stage(item, has_script, has_approved_script, has_poster, has_approved_poster)
        if stage != 'approved':
            return err('This item must reach the Approved stage before it can be published.')

        item.status = PlanItem.Status.POSTED
        item.published_at = timezone.now()
        item.published_by = user
        item.save(update_fields=['status', 'published_at', 'published_by', 'updated_at'])
        return ok({'status': item.status}, message='Marked as published.')

    def _accept_proposal(self, item, user):
        if not item.proposed_changes:
            return err('This item has no pending proposed change.')
        proposed = item.proposed_changes
        fields = []
        if 'working_title' in proposed:
            item.working_title = (proposed.get('working_title') or item.working_title)[:255]
            fields.append('working_title')
        if 'context_notes' in proposed:
            item.context_notes = proposed.get('context_notes') or ''
            fields.append('context_notes')
        item.proposed_changes = {}
        item.save(update_fields=fields + ['proposed_changes', 'updated_at'])
        ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED, status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DONE, resolved_by=user, resolved_at=timezone.now())
        return ok({
            'working_title': item.working_title, 'context_notes': item.context_notes,
        }, message='Proposed change applied.')

    def _reject_proposal(self, item, user):
        if not item.proposed_changes:
            return err('This item has no pending proposed change.')
        item.proposed_changes = {}
        item.save(update_fields=['proposed_changes', 'updated_at'])
        ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED, status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DISMISSED, resolved_by=user, resolved_at=timezone.now())
        return ok(message='Proposed change dismissed — item left unchanged.')

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


class RegeneratePlanAPI(APIView):
    """Plan-level "Regenerate" (content-generator-plan.md's lifecycle
    follow-up): re-runs the Planner for a plan that already exists.

    Deletes every item that hasn't been touched yet — still PLANNED (the
    admin never approved or skipped it), with no Script and no PosterAsset
    — then re-runs generate_plan for the same period, so the freed slots
    get fresh proposals and any date/content_type combo still missing from
    the period gets filled in. NEVER touches an item that's been approved,
    skipped, drafted, or generated in any way — an edited-but-unapproved
    item (update_item leaves status PLANNED) is NOT protected and will be
    discarded and replaced; approving is what protects an item.

    An optional free-text `instruction` steers the fresh proposals for the
    freed/open slots (e.g. "add a poster about X on the 20th") — it can't
    affect anything already fixed in the plan, since those items are never
    deleted and generate_plan's own existing_keys dedup won't duplicate them."""
    permission_classes = [HasModulePermission]
    permission_module = 'content_calendar'

    def post(self, request):
        brand_id = current_brand_id(request)
        try:
            plan = ContentPlan.objects.get(id=int(request.data.get('plan_id') or 0), brand_id=brand_id)
        except (ContentPlan.DoesNotExist, TypeError, ValueError):
            return err('Plan not found.', status=404)
        if plan.status == ContentPlan.Status.GENERATING:
            return err('A plan is already being generated for this period.',
                       data={'plan_id': plan.id})

        instruction = (request.data.get('instruction') or '').strip()
        plan.status = ContentPlan.Status.GENERATING
        plan.generation_error = ''
        plan.save(update_fields=['status', 'generation_error', 'updated_at'])
        run_planner_regenerate_task.delay(plan.id, instruction=instruction)
        return ok({'plan_id': plan.id, 'status': plan.status}, message='Regenerating plan…')


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
        topic_asset = designer.select_brand_asset(brand, tag=BrandAsset.Tag.PRODUCT)
        brand_logo = designer.select_brand_asset(brand, tag=BrandAsset.Tag.LOGO)

        try:
            brief = designer.generate_poster_brief(
                brand, topic, inspiration, context_notes=context_notes, has_brand_logo=bool(brand_logo),
            )
        except Exception as exc:
            return err(f'Brief generation failed: {exc}')

        image_bytes, debug = designer.generate_poster_image(
            brief, inspiration, topic_asset=topic_asset, brand_logo=brand_logo,
            format=format, brand_name=brand.name,
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
