"""
Poster review admin page (content-generator-plan.md §13 Phase 3, §12's
"Poster review" bullet; poster-generation-plan.md §7's versioning/edit-loop
spec). New file, deliberately NOT merged into content/views.py — see
content-generator-plan.md and the Phase-3 task brief this was built
against for why (a sibling agent owns content/views.py concurrently).

Mirrors ContentCalendarAPI's dispatch style (§21-23): GET lists/details,
POST dispatches on `action`. The four actions here are the review
lifecycle for one PosterAsset: approve / request_changes / regenerate /
retry. `regenerate` and `retry` both delegate to
content.agents.designer.regenerate_poster — retry is just "regenerate with
no instruction, same inspiration" for a specifically FAILED attempt
(image is None), so an admin doesn't have to type anything to ask for a
fresh try at a broken generation.

`PosterAsset.review_notes`/`reviewed_by`/`reviewed_at` (migration 0007)
mirror `Script`'s equivalent fields — request_changes' notes used to be
stashed inside `generation_metadata` under a `review_notes` key (a stopgap
from when this file was built under a "no migrations" constraint); that's
now a real column, set exactly like Script's `_request_changes`.
"""
from django.db.models import Max
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.views import APIView

from core.api import HasModulePermission, current_brand_id, err, ok
from core.auth import admin_login_required, require_module

from content.agents import designer
from content.models import ActionItem, PosterAsset, PosterInspiration


@admin_login_required
@require_module('content_posters')
@ensure_csrf_cookie
def content_posters_page(request):
    return render(request, 'admin/content-posters.html')


def _poster_list_dict(p):
    return {
        'id': p.id,
        'plan_item_id': p.plan_item_id,
        'working_title': p.plan_item.working_title,
        'content_type': p.plan_item.content_type,
        'format': p.format,
        'version': p.version,
        'status': p.status,
        'created_at': p.created_at.isoformat(),
        'image_url': p.image.url if p.image else None,
        'failed': not bool(p.image),
    }


def _poster_detail_dict(p, brand_id):
    metadata = p.generation_metadata or {}
    return {
        'id': p.id,
        'plan_item_id': p.plan_item_id,
        'working_title': p.plan_item.working_title,
        'content_type': p.plan_item.content_type,
        'context_notes': p.plan_item.context_notes,
        'format': p.format,
        'version': p.version,
        'status': p.status,
        'created_at': p.created_at.isoformat(),
        'updated_at': p.updated_at.isoformat(),
        'image_url': p.image.url if p.image else None,
        'failed': not bool(p.image),
        'brief_text': (p.brief or {}).get('text') if isinstance(p.brief, dict) else p.brief,
        'generation_metadata': metadata,
        'review_notes': p.review_notes,
        'reviewed_by': p.reviewed_by.name if p.reviewed_by_id else None,
        'reviewed_at': p.reviewed_at.isoformat() if p.reviewed_at else None,
        'inspiration_id': p.inspiration_id,
        'inspiration_label': p.inspiration.source_label if p.inspiration else None,
        'eligible_inspirations': list(
            PosterInspiration.objects.filter(brand_id=brand_id, is_active=True)
            .order_by('source_label')
            .values('id', 'source_label', 'topic_category', 'format'),
        ),
    }


def _is_latest_version(poster):
    latest = PosterAsset.objects.filter(plan_item_id=poster.plan_item_id).aggregate(
        Max('version'))['version__max']
    return poster.version == latest


class PostersAPI(APIView):
    """
    GET  (no `id`)  -> list, optionally `?status=needs_review`.
    GET  (`id=<n>`) -> full detail (brief, generation_metadata, eligible
                       inspirations for the "swap reference" control).
    POST action=approve         -> status=APPROVED (rejects a stale/non-
                                    latest version, or a failed attempt).
    POST action=request_changes -> status=CHANGES_REQUESTED, optional
                                    `notes` (see module docstring on where
                                    these are stored).
    POST action=regenerate      -> designer.regenerate_poster(instruction,
                                    inspiration_id) -> new PosterAsset row.
    POST action=retry           -> same as regenerate but for a specifically
                                    FAILED attempt (image is None), no
                                    instruction needed from the admin.
    """
    permission_classes = [HasModulePermission]
    permission_module = 'content_posters'

    def get(self, request):
        brand_id = current_brand_id(request)
        poster_id = request.query_params.get('id')

        if poster_id:
            try:
                poster_id = int(poster_id)
            except (TypeError, ValueError):
                return err('id must be an integer.')
            poster = PosterAsset.objects.filter(
                id=poster_id, plan_item__plan__brand_id=brand_id,
            ).select_related('plan_item', 'inspiration').first()
            if not poster:
                return err('Poster not found.', status=404)
            return ok(_poster_detail_dict(poster, brand_id))

        qs = PosterAsset.objects.filter(
            plan_item__plan__brand_id=brand_id,
        ).select_related('plan_item').order_by('-created_at')
        status = request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        return ok({'items': [_poster_list_dict(p) for p in qs]})

    def post(self, request):
        brand_id = current_brand_id(request)
        action = request.data.get('action')

        try:
            poster_id = int(request.data.get('id') or 0)
        except (TypeError, ValueError):
            return err('id is required.')
        poster = PosterAsset.objects.filter(
            id=poster_id, plan_item__plan__brand_id=brand_id,
        ).select_related('plan_item__plan__brand', 'inspiration').first()
        if not poster:
            return err('Poster not found.', status=404)

        if action == 'approve':
            return self._approve(poster, request.user)
        if action == 'request_changes':
            return self._request_changes(poster, request.data, request.user)
        if action == 'regenerate':
            return self._regenerate(poster, request.data)
        if action == 'retry':
            return self._retry(poster)
        return err('Unknown action.')

    def _approve(self, poster, user):
        if not _is_latest_version(poster):
            return err('A newer attempt exists for this item — review that one instead.')
        if poster.status == PosterAsset.Status.APPROVED:
            return err('This poster is already approved.')
        if not poster.image:
            return err('This attempt failed to generate an image — retry it before approving.')
        poster.status = PosterAsset.Status.APPROVED
        poster.reviewed_by = user
        poster.reviewed_at = timezone.now()
        poster.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        ActionItem.objects.filter(
            plan_item=poster.plan_item, kind=ActionItem.Kind.POSTER_REVIEW,
            status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DONE, resolved_by=user, resolved_at=timezone.now())
        return ok({'status': poster.status}, message='Poster approved.')

    def _request_changes(self, poster, data, user):
        if not _is_latest_version(poster):
            return err('A newer attempt exists for this item — review that one instead.')
        poster.review_notes = (data.get('notes') or '').strip()
        poster.status = PosterAsset.Status.CHANGES_REQUESTED
        poster.reviewed_by = user
        poster.reviewed_at = timezone.now()
        poster.save(update_fields=['status', 'review_notes', 'reviewed_by', 'reviewed_at', 'updated_at'])
        return ok({'status': poster.status}, message='Changes requested.')

    def _regenerate(self, poster, data):
        instruction = (data.get('instruction') or '').strip()
        raw_inspiration_id = data.get('inspiration_id')
        try:
            inspiration_id = int(raw_inspiration_id) if raw_inspiration_id not in (None, '') else None
        except (TypeError, ValueError):
            return err('inspiration_id must be an integer.')
        try:
            new_poster = designer.regenerate_poster(
                poster.plan_item, instruction=instruction, inspiration_id=inspiration_id,
            )
        except Exception as exc:
            return err(f'Regeneration failed: {exc}')
        return ok({
            'id': new_poster.id, 'version': new_poster.version, 'status': new_poster.status,
        }, message='Poster regenerated — review the new version.')

    def _retry(self, poster):
        """Retries a specifically FAILED attempt (image=None) — reuses the
        same inspiration, no instruction, same idea as
        content-calendar.html's regenerate but with nothing for the admin
        to type. Always creates a new version (never mutates the failed
        row), same as `regenerate`."""
        if poster.image:
            return err('This attempt already produced an image — use "Regenerate" instead of retry.')
        try:
            new_poster = designer.regenerate_poster(
                poster.plan_item, instruction='', inspiration_id=poster.inspiration_id,
            )
        except Exception as exc:
            return err(f'Retry failed: {exc}')
        return ok({
            'id': new_poster.id, 'version': new_poster.version, 'status': new_poster.status,
        }, message='Retried — review the new attempt.')
