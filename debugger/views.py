"""
Debugger Agent — super-admin-only page + JSON API.

Mirrors the Brands slice (core/brands_views.py): the page view redirects
non-super-admins, and the API re-checks is_super_admin on every request (the
shared HasModulePermission never *excludes* a regular admin, so an explicit
gate is required). Responses use the standard {success, message, data} envelope.
"""
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.permissions import BasePermission
from rest_framework.views import APIView

from core.api import err, ok
from core.auth import admin_login_required
from debugger.models import DebugLearning, DebugMessage, DebugRequest


@admin_login_required
@ensure_csrf_cookie
def debugger_page(request):
    if not getattr(request.user, 'is_super_admin', False):
        return redirect('core:dashboard')
    return render(request, 'admin/debugger.html')


class IsSuperAdmin(BasePermission):
    message = 'Super admin only.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated
                    and getattr(user, 'is_super_admin', False))


# Statuses where the backend is actively working — the UI polls these.
LIVE_STATUSES = {
    DebugRequest.Status.NEW, DebugRequest.Status.ANALYZING,
    DebugRequest.Status.PR_REQUESTED, DebugRequest.Status.REVISING,
    DebugRequest.Status.CHANGES_REQUESTED,
}


def _msg_dict(m):
    return {
        'id': m.id,
        'role': m.role,
        'content': m.content,
        'meta': m.meta,
        'created_at': m.created_at.isoformat(),
    }


def _req_summary(r):
    return {
        'id': r.id,
        'kind': r.kind,
        'title': r.title,
        'status': r.status,
        'has_proposal': bool(r.proposed_diff),
        'pr_url': r.pr_url,
        'created_at': r.created_at.isoformat(),
        'updated_at': r.updated_at.isoformat(),
    }


def _req_detail(r):
    d = _req_summary(r)
    d.update({
        'body': r.body,
        'rca': r.rca,
        'proposed_diff': r.proposed_diff,
        'pr_title': r.pr_title,
        'pr_body': r.pr_body,
        'pr_branch': r.pr_branch,
        'error': r.error,
        'is_live': r.status in LIVE_STATUSES,
        'messages': [_msg_dict(m) for m in r.messages.all()],
    })
    return d


def _enqueue(request_id, mode=None):
    """Best-effort enqueue; broker being down must not lose the saved request."""
    from debugger.tasks import process_request
    try:
        process_request.delay(request_id, mode=mode)
        return True
    except Exception:
        return False


def _enqueue_task(task, *args):
    try:
        task.delay(*args)
        return True
    except Exception:
        return False


class DebuggerAPI(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        req_id = request.query_params.get('id')
        if req_id:
            r = DebugRequest.objects.filter(id=req_id).first()
            if not r:
                return err('Request not found.', status=404)
            return ok(_req_detail(r))

        qs = DebugRequest.objects.all()
        kind = request.query_params.get('kind')
        status = request.query_params.get('status')
        if kind:
            qs = qs.filter(kind=kind)
        if status:
            qs = qs.filter(status=status)
        return ok({'requests': [_req_summary(r) for r in qs[:200]]})

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action', '')
        handler = {
            'create': self._create,
            'reply': self._reply,
            'close': self._close,
            'request_pr': self._request_pr,
            'approve_plan': self._approve_plan,
            'sync_pr': self._sync_pr,
        }.get(action)
        if not handler:
            return err('Unknown action.')
        return handler(request, body)

    def _create(self, request, body):
        kind = (body.get('kind') or '').strip()
        title = (body.get('title') or '').strip()
        text = (body.get('body') or '').strip()
        if kind not in DebugRequest.Kind.values:
            return err('Invalid kind.')
        if not title:
            return err('Title is required.')
        r = DebugRequest.objects.create(
            kind=kind, title=title[:200], body=text, created_by=request.user,
        )
        if text:
            DebugMessage.objects.create(
                request=r, role=DebugMessage.Role.ADMIN, content=text,
            )
        queued = _enqueue(r.id)
        return ok(
            {'id': r.id, 'queued': queued},
            message='Request created.' if queued else
                    'Request created, but the worker queue is unavailable.',
        )

    def _reply(self, request, body):
        r = DebugRequest.objects.filter(id=body.get('id')).first()
        if not r:
            return err('Request not found.', status=404)
        if r.status == DebugRequest.Status.CLOSED:
            return err('This thread is closed.')
        content = (body.get('content') or '').strip()
        if not content:
            return err('Message is empty.')
        DebugMessage.objects.create(
            request=r, role=DebugMessage.Role.ADMIN, content=content,
        )
        r.status = DebugRequest.Status.NEW
        r.save(update_fields=['status', 'updated_at'])
        queued = _enqueue(r.id)
        return ok({'queued': queued}, message='Reply sent.')

    def _close(self, request, body):
        r = DebugRequest.objects.filter(id=body.get('id')).first()
        if not r:
            return err('Request not found.', status=404)
        # Phase 3 will run finalize_learning here before closing.
        r.status = DebugRequest.Status.CLOSED
        r.save(update_fields=['status', 'updated_at'])
        DebugMessage.objects.create(
            request=r, role=DebugMessage.Role.SYSTEM, content='Thread closed.',
        )
        return ok(message='Thread closed.')

    def _request_pr(self, request, body):
        from debugger.tasks import create_pr
        r = DebugRequest.objects.filter(id=body.get('id')).first()
        if not r:
            return err('Request not found.', status=404)
        if not (r.proposed_diff or '').strip():
            return err('There is no proposed fix to open a PR for.')
        if r.pr_url:
            return err('A PR is already open for this thread.')
        r.status = DebugRequest.Status.PR_REQUESTED
        r.save(update_fields=['status', 'updated_at'])
        DebugMessage.objects.create(
            request=r, role=DebugMessage.Role.SYSTEM,
            content='PR requested — opening a branch and pull request…')
        queued = _enqueue_task(create_pr, r.id)
        return ok({'queued': queued}, message='Opening pull request…')

    def _approve_plan(self, request, body):
        """Feature flow: admin approves the plan → agent generates the diff."""
        r = DebugRequest.objects.filter(id=body.get('id')).first()
        if not r:
            return err('Request not found.', status=404)
        if r.status == DebugRequest.Status.CLOSED:
            return err('This thread is closed.')
        DebugMessage.objects.create(
            request=r, role=DebugMessage.Role.ADMIN,
            content='Plan approved. Please generate the implementation as a '
                    'unified diff and call propose_fix so I can open a PR.')
        r.status = DebugRequest.Status.NEW
        r.save(update_fields=['status', 'updated_at'])
        queued = _enqueue(r.id)
        return ok({'queued': queued}, message='Plan approved — generating the fix.')

    def _sync_pr(self, request, body):
        from debugger.tasks import sync_pr_reviews
        r = DebugRequest.objects.filter(id=body.get('id')).first()
        if not r:
            return err('Request not found.', status=404)
        if not r.pr_url:
            return err('No PR is open for this thread.')
        queued = _enqueue_task(sync_pr_reviews, r.id)
        return ok({'queued': queued}, message='Checking for new PR review activity…')
