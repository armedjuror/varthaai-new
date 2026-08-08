"""
Celery tasks for the Debugger Agent.

Phase 1: `process_request` runs the read-only RCA/feature/query agent.
Phase 2: PR lifecycle — `create_pr`, `revise_pr`, and the review-ingestion loop
(`sync_pr_reviews` + the `poll_open_prs` beat task).

Guarantees: PR work happens only in the isolated clone (debugger/pr.py); the
never-push-main guard lives there. The agent never performs git/GitHub actions.
"""
import time

from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


def _sys(request, content):
    from debugger.models import DebugMessage
    DebugMessage.objects.create(
        request=request, role=DebugMessage.Role.SYSTEM, content=content)


def _is_max_turns_error(exc):
    """The Claude Agent SDK reports a hit `max_turns` cap as a bare Exception
    with this substring (see claude_agent_sdk._internal.query.receive_messages)
    — there's no dedicated exception type to catch."""
    return 'maximum number of turns' in str(exc).lower()


# --------------------------------------------------------------------------- #
# Analysis (Phase 1 + review-mode revision)                                   #
# --------------------------------------------------------------------------- #
@shared_task(bind=True, max_retries=0)
def process_request(self, request_id, mode=None):
    from debugger.agent import run_agent
    from debugger.models import DebugMessage, DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request:
        logger.warning('process_request: request %s not found', request_id)
        return
    if request.status == DebugRequest.Status.CLOSED:
        return

    prev_status = request.status
    request.status = DebugRequest.Status.ANALYZING
    request.error = ''
    request.save(update_fields=['status', 'error', 'updated_at'])

    # Review mode reads the PR-branch worktree so its diff is a delta on top of
    # the existing fix. Fall back to the main checkout if the branch isn't ready.
    cwd = None
    if mode == 'review':
        try:
            from debugger import pr
            cwd = pr.checkout_branch(request)
        except Exception as exc:
            logger.warning('review checkout failed for %s: %s', request_id, exc)
            cwd = None

    start = time.monotonic()
    try:
        result = run_agent(request, cwd=cwd, mode=mode)
    except SoftTimeLimitExceeded:
        elapsed = time.monotonic() - start
        logger.warning('process_request timed out for %s after %.0fs', request_id, elapsed)
        request.status = DebugRequest.Status.FAILED
        request.error = f'Timed out after {elapsed:.0f}s'
        request.save(update_fields=['status', 'error', 'updated_at'])
        _sys(request, (
            f'Analysis timed out after {elapsed:.0f}s. It may have been running a '
            'broad search over the codebase — try narrowing the request (mention a '
            'specific file/module) and re-run.'
        ))
        return
    except Exception as exc:
        logger.exception('process_request failed for %s', request_id)
        request.status = DebugRequest.Status.FAILED
        request.error = str(exc)
        request.save(update_fields=['status', 'error', 'updated_at'])
        if _is_max_turns_error(exc):
            _sys(request, (
                'Analysis stopped without finishing — it used up all of its '
                'steps investigating. This usually happens on broad or '
                'multi-part requests. Try breaking it into smaller, more '
                'specific questions (e.g. one model/file/flow at a time) and '
                're-run.'
            ))
        else:
            _sys(request, f'Analysis failed: {exc}')
        return

    text = result.get('text') or '(the agent returned no text)'
    proposals = result.get('proposals') or []

    DebugMessage.objects.create(
        request=request, role=DebugMessage.Role.AGENT, content=text,
        meta={
            'tools_used': result.get('tools_used'),
            'usage': result.get('usage'),
            'has_proposal': bool(proposals),
            'mode': mode,
        },
    )

    fields = ['status', 'updated_at']
    if request.kind in (DebugRequest.Kind.BUG, DebugRequest.Kind.FEATURE):
        request.rca = text
        fields.append('rca')

    if proposals:
        from debugger import pr
        p = proposals[-1]
        request.proposed_diff = p.get('diff', '')
        request.pr_title = (p.get('pr_title', '') or request.pr_title or request.title)[:200]
        request.pr_body = p.get('pr_body', '') or request.pr_body
        # Record the commit the agent read so the PR can transplant the fix even
        # after the default branch moves on (see pr.create_pull_request).
        request.base_sha = pr.current_code_sha(cwd) or request.base_sha
        fields += ['proposed_diff', 'pr_title', 'pr_body', 'base_sha']
        if request.pr_url:
            # A PR already exists → this proposal is a revision. Push it.
            request.status = DebugRequest.Status.REVISING
            request.save(update_fields=list(set(fields)))
            revise_pr.delay(request.id)
            return
        # No PR yet → wait for the admin to click "Create PR".
        request.status = DebugRequest.Status.READY
    else:
        # Answered / asked a question — back to the admin.
        request.status = DebugRequest.Status.AWAITING_INPUT

    request.save(update_fields=list(set(fields)))
    return {'request_id': request_id, 'status': request.status}


# --------------------------------------------------------------------------- #
# PR creation / revision                                                       #
# --------------------------------------------------------------------------- #
@shared_task(bind=True, max_retries=0)
def create_pr(self, request_id):
    from debugger import pr
    from debugger.models import DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request:
        return
    if not request.proposed_diff.strip():
        _sys(request, 'Cannot open a PR — no proposed fix is attached.')
        request.status = DebugRequest.Status.AWAITING_INPUT
        request.save(update_fields=['status', 'updated_at'])
        return

    start = time.monotonic()
    try:
        info = pr.create_pull_request(request)
    except SoftTimeLimitExceeded:
        elapsed = time.monotonic() - start
        logger.warning('create_pr timed out for %s after %.0fs', request_id, elapsed)
        request.status = DebugRequest.Status.READY
        request.error = f'Timed out after {elapsed:.0f}s'
        request.save(update_fields=['status', 'error', 'updated_at'])
        _sys(request, f'Could not open PR: timed out after {elapsed:.0f}s.')
        return
    except Exception as exc:
        logger.exception('create_pr failed for %s', request_id)
        request.status = DebugRequest.Status.READY
        request.error = str(exc)
        request.save(update_fields=['status', 'error', 'updated_at'])
        _sys(request, f'Could not open PR: {exc}')
        return

    request.pr_url = info['pr_url']
    request.pr_branch = info['branch']
    request.status = DebugRequest.Status.PR_OPEN
    request.error = ''
    request.save(update_fields=['pr_url', 'pr_branch', 'status', 'error', 'updated_at'])
    _sys(request, f'Pull request opened on branch `{info["branch"]}`: {info["pr_url"]}')
    return info


@shared_task(bind=True, max_retries=0)
def revise_pr(self, request_id):
    from debugger import pr
    from debugger.models import DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request or not request.pr_branch:
        return
    start = time.monotonic()
    try:
        branch = pr.push_revision(request, request.proposed_diff)
        if request.pr_url:
            num = pr.pr_number_from_url(request.pr_url)
            if num:
                pr.comment_on_pr(num, 'Pushed a revision addressing the review feedback.')
    except SoftTimeLimitExceeded:
        elapsed = time.monotonic() - start
        logger.warning('revise_pr timed out for %s after %.0fs', request_id, elapsed)
        request.status = DebugRequest.Status.CHANGES_REQUESTED
        request.error = f'Timed out after {elapsed:.0f}s'
        request.save(update_fields=['status', 'error', 'updated_at'])
        _sys(request, f'Could not push the revision: timed out after {elapsed:.0f}s.')
        return
    except Exception as exc:
        logger.exception('revise_pr failed for %s', request_id)
        request.status = DebugRequest.Status.CHANGES_REQUESTED
        request.error = str(exc)
        request.save(update_fields=['status', 'error', 'updated_at'])
        _sys(request, f'Could not push the revision: {exc}')
        return

    request.status = DebugRequest.Status.PR_UPDATED
    request.error = ''
    request.save(update_fields=['status', 'error', 'updated_at'])
    _sys(request, f'Revision pushed to `{branch}`.')


# --------------------------------------------------------------------------- #
# PR-review ingestion loop                                                     #
# --------------------------------------------------------------------------- #
@shared_task(bind=True, max_retries=0)
def sync_pr_reviews(self, request_id):
    from debugger import pr
    from debugger.models import DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request or not request.pr_url:
        return
    num = pr.pr_number_from_url(request.pr_url)
    if not num:
        return
    if request.status in (DebugRequest.Status.ANALYZING,
                          DebugRequest.Status.REVISING):
        return  # busy; don't double-trigger

    try:
        activity = pr.fetch_pr_activity(
            num, request.last_seen_review_id, request.last_seen_comment_id)
    except Exception as exc:
        logger.warning('sync_pr_reviews failed for %s: %s', request_id, exc)
        return

    items = activity['items']
    # Always advance the watermark so nothing is re-ingested.
    request.last_seen_review_id = activity['max_review_id'] or request.last_seen_review_id
    request.last_seen_comment_id = activity['max_comment_id'] or request.last_seen_comment_id

    if not items:
        request.save(update_fields=['last_seen_review_id', 'last_seen_comment_id', 'updated_at'])
        return 0

    for it in items:
        if it['kind'] == 'review':
            head = f'PR review by @{it["author"]} ({it.get("state", "")})'
        else:
            loc = f' on `{it["path"]}`' if it.get('path') else ''
            head = f'PR comment by @{it["author"]}{loc}'
        _sys(request, f'{head}:\n{it.get("body", "").strip() or "(no text)"}')

    request.status = DebugRequest.Status.CHANGES_REQUESTED
    request.save(update_fields=[
        'last_seen_review_id', 'last_seen_comment_id', 'status', 'updated_at'])
    # Hand off to the agent to analyse the feedback and revise or reply.
    process_request.delay(request.id, mode='review')
    return len(items)


@shared_task(bind=True, max_retries=0)
def finalize_learning(self, request_id):
    """
    On close, have the agent distil a reusable lesson from the thread and stage
    it for the admin to review/edit before it is saved as a DebugLearning.
    """
    from debugger.agent import run_agent
    from debugger.models import DebugMessage, DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request or request.status == DebugRequest.Status.CLOSED:
        return

    request.status = DebugRequest.Status.FINALIZING
    request.save(update_fields=['status', 'updated_at'])

    title, draft = '', ''
    start = time.monotonic()
    try:
        result = run_agent(request, mode='finalize')
        text = result.get('text') or ''
        if text.strip():
            DebugMessage.objects.create(
                request=request, role=DebugMessage.Role.AGENT, content=text,
                meta={'mode': 'finalize', 'usage': result.get('usage')})
        learnings = result.get('learnings') or []
        if learnings:
            title = learnings[-1].get('title', '')
            draft = learnings[-1].get('content', '')
    except SoftTimeLimitExceeded:
        elapsed = time.monotonic() - start
        logger.warning('finalize_learning timed out for %s after %.0fs', request_id, elapsed)
        _sys(request, f'Could not draft a learning automatically (timed out after '
                      f'{elapsed:.0f}s). You can write one or close without saving.')
    except Exception as exc:
        logger.exception('finalize_learning failed for %s', request_id)
        _sys(request, f'Could not draft a learning automatically ({exc}). '
                      'You can write one or close without saving.')

    request.learning_title = (title or request.title)[:200]
    request.learning_draft = draft
    request.status = DebugRequest.Status.LEARNING_REVIEW
    request.save(update_fields=['learning_title', 'learning_draft', 'status', 'updated_at'])
    return {'request_id': request_id, 'has_draft': bool(draft)}


def requeue_orphaned_requests(min_age_seconds=90):
    """
    Re-dispatch requests left in a worker-owned transient status by a crash or a
    Celery restart. Called from the worker_ready signal (see debugger/apps.py)
    so a bounced worker resumes investigations that were mid-flight instead of
    leaving them stuck on 'Analyzing' forever.

    Status → recovery:
      ANALYZING  -> process_request (review mode if a PR is already open)
      REVISING   -> revise_pr
      FINALIZING -> finalize_learning

    The min_age guard (compared against updated_at, which is stamped when the
    task enters the transient status) avoids stealing a task a peer worker only
    just started; the deployment runs a single worker, so this is belt-and-braces.
    Returns the number of requests re-dispatched.
    """
    from datetime import timedelta

    from django.utils import timezone

    from debugger.models import DebugRequest

    cutoff = timezone.now() - timedelta(seconds=min_age_seconds)
    S = DebugRequest.Status
    requeued = 0

    for req in DebugRequest.objects.filter(status=S.ANALYZING, updated_at__lt=cutoff):
        mode = 'review' if req.pr_url else None
        _sys(req, 'Worker restarted mid-analysis — re-queuing this investigation.')
        process_request.delay(req.id, mode=mode)
        requeued += 1

    for req in DebugRequest.objects.filter(status=S.REVISING, updated_at__lt=cutoff):
        _sys(req, 'Worker restarted mid-revision — re-queuing.')
        revise_pr.delay(req.id)
        requeued += 1

    for req in DebugRequest.objects.filter(status=S.FINALIZING, updated_at__lt=cutoff):
        finalize_learning.delay(req.id)
        requeued += 1

    if requeued:
        logger.info('requeue_orphaned_requests: re-dispatched %s stuck request(s)', requeued)
    return requeued


@shared_task(bind=True, max_retries=0)
def poll_open_prs(self):
    """Beat task: pull new review activity for every live PR thread."""
    from debugger.models import DebugRequest

    open_statuses = [DebugRequest.Status.PR_OPEN, DebugRequest.Status.PR_UPDATED]
    ids = list(DebugRequest.objects.filter(status__in=open_statuses)
               .exclude(pr_url='').values_list('id', flat=True))
    for rid in ids:
        sync_pr_reviews.delay(rid)
    return len(ids)
