"""
Script review admin page + API (content-generator-plan.md §13 Phase 2 /
§12's "Script review" page). Deliberately a SEPARATE module from
content/views.py (not added to it) — kept isolated per this phase's build
instructions since content/views.py was being touched by a concurrent
sibling agent's work.

Same envelope/brand-scoping/approval-discipline conventions as
content/views.py's ContentCalendarAPI: {success, message, data} via
core.api.ok/err, HasModulePermission + permission_module, brand-scoped
through plan_item__plan__brand_id, and "reject once already approved/stale"
guards before mutating anything.
"""
from django.db.models import Max
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module

from content.agents import copywriter
from content.models import ActionItem, PlanItem, Script


@admin_login_required
@require_module('content_scripts')
@ensure_csrf_cookie
def content_scripts_page(request):
    return render(request, 'admin/content-scripts.html')


def _script_list_dict(s):
    preview = (s.raw_output or '')[:220]
    return {
        'id': s.id,
        'plan_item_id': s.plan_item_id,
        'working_title': s.plan_item.working_title,
        'planned_date': s.plan_item.planned_date.isoformat(),
        'content_type': s.plan_item.content_type,
        'skill_used': s.skill_used,
        'version': s.version,
        'status': s.status,
        'created_at': s.created_at.isoformat(),
        'preview': preview + ('…' if len(s.raw_output or '') > 220 else ''),
    }


def _script_detail_dict(s):
    return {
        'id': s.id,
        'plan_item_id': s.plan_item_id,
        'working_title': s.plan_item.working_title,
        'planned_date': s.plan_item.planned_date.isoformat(),
        'content_type': s.plan_item.content_type,
        'context_notes': s.plan_item.context_notes,
        'skill_used': s.skill_used,
        'version': s.version,
        'status': s.status,
        'raw_output': s.raw_output,
        'structured_json': s.structured_json,
        'recap_summary': s.recap_summary,
        'review_notes': s.review_notes,
        'created_at': s.created_at.isoformat(),
    }


class ScriptsAPI(APIView):
    """
    GET             -> list, ONE ROW PER PLAN ITEM (§27) — a plan item's own
                       older/superseded versions aren't separate "pending"
                       rows; the list shows whichever version is the
                       relevant one (the APPROVED version if one exists,
                       else the latest), with the full `versions` list
                       alongside it for the review modal's version switcher.
                       Filterable by `status` (matched against that
                       relevant/default version's status, not every version).
    GET ?id=<id>    -> full detail for one specific Script version.
    POST action=draft|approve|request_changes|regenerate.
    """
    permission_classes = [HasModulePermission]
    permission_module = 'content_scripts'

    def get(self, request):
        brand_id = current_brand_id(request)
        script_id = request.query_params.get('id')
        if script_id:
            try:
                script = Script.objects.select_related('plan_item').get(
                    id=int(script_id), plan_item__plan__brand_id=brand_id)
            except (Script.DoesNotExist, TypeError, ValueError):
                return err('Script not found.', status=404)
            return ok(_script_detail_dict(script))

        status = request.query_params.get('status')
        scripts = (
            Script.objects.filter(plan_item__plan__brand_id=brand_id)
            .select_related('plan_item').order_by('plan_item_id', '-version')
        )
        grouped = {}
        for s in scripts:
            grouped.setdefault(s.plan_item_id, []).append(s)

        items = []
        for versions in grouped.values():
            # versions is already ordered -version (latest first) within
            # the group, so versions[0] is the latest whenever no version
            # is APPROVED.
            default = next((v for v in versions if v.status == Script.Status.APPROVED), versions[0])
            if status and default.status != status:
                continue
            d = _script_list_dict(default)
            d['versions'] = [{'id': v.id, 'version': v.version, 'status': v.status} for v in versions]
            items.append(d)

        items.sort(key=lambda d: d['created_at'], reverse=True)
        return ok({'items': items})

    def post(self, request):
        """action=draft|approve|request_changes|regenerate. `draft` is the
        one action that takes `plan_item_id` instead of `script_id` —
        content-generator-plan.md §24's manual trigger. Always creates a NEW
        version (§27: "generate script again" on an item that already has
        one is the same action, not a separate one — draft_now() already
        computes version = max+1 regardless of whether any prior version
        exists)."""
        brand_id = current_brand_id(request)
        action = request.data.get('action')

        if action == 'draft':
            return self._draft(request, brand_id)

        try:
            script = Script.objects.select_related('plan_item', 'plan_item__plan').get(
                id=int(request.data.get('script_id') or 0), plan_item__plan__brand_id=brand_id)
        except (Script.DoesNotExist, TypeError, ValueError):
            return err('Script not found.', status=404)

        if action == 'approve':
            return self._approve(script, request)
        if action == 'request_changes':
            return self._request_changes(script, request)
        if action == 'regenerate':
            return self._regenerate(script, request)
        return err('Unknown action.')

    def _draft(self, request, brand_id):
        try:
            item = PlanItem.objects.select_related('plan').get(
                id=int(request.data.get('plan_item_id') or 0), plan__brand_id=brand_id)
        except (PlanItem.DoesNotExist, TypeError, ValueError):
            return err('Plan item not found.', status=404)
        try:
            script = copywriter.draft_now(item)
        except Exception as exc:
            return err(f'Draft failed: {exc}')
        return ok(_script_detail_dict(script), message='Script drafted — review it below.')

    def _latest_version(self, plan_item):
        return plan_item.scripts.aggregate(Max('version'))['version__max']

    def _approve(self, script, request):
        if script.status == Script.Status.APPROVED:
            return err('This script is already approved.')
        if script.version != self._latest_version(script.plan_item):
            return err('This is not the latest version of this script — a newer draft exists.')

        script.status = Script.Status.APPROVED
        script.reviewed_by = request.user
        script.reviewed_at = timezone.now()
        script.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])

        item = script.plan_item
        item.status = PlanItem.Status.APPROVED
        item.save(update_fields=['status', 'updated_at'])

        ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.SCRIPT_REVIEW, status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DONE, resolved_by=request.user, resolved_at=timezone.now())

        return ok({'status': script.status, 'plan_item_status': item.status}, message='Script approved.')

    def _request_changes(self, script, request):
        if script.status == Script.Status.APPROVED:
            return err('This script is already approved — regenerate instead of requesting changes.')
        if script.version != self._latest_version(script.plan_item):
            return err('This is not the latest version of this script — a newer draft exists.')

        script.status = Script.Status.CHANGES_REQUESTED
        script.review_notes = (request.data.get('review_notes') or '').strip()
        script.reviewed_by = request.user
        script.reviewed_at = timezone.now()
        script.save(update_fields=['status', 'review_notes', 'reviewed_by', 'reviewed_at', 'updated_at'])
        # plan_item.status deliberately left as NEEDS_APPROVAL — still
        # actionable (e.g. regenerate), same "each control does one thing"
        # rule content/views.py's update_item/toggle actions already follow.
        return ok({'status': script.status}, message='Changes requested.')

    def _regenerate(self, script, request):
        if script.version != self._latest_version(script.plan_item):
            return err('This is not the latest version of this script — a newer draft exists.')

        instruction = (request.data.get('instruction') or '').strip()
        try:
            new_script = copywriter.regenerate_script(script, instruction=instruction)
        except Exception as exc:
            return err(f'Regeneration failed: {exc}')
        return ok(
            _script_detail_dict(new_script),
            message='Script regenerated — review the new draft.',
        )
