"""
Celery tasks for the Debugger Agent.

`process_request` runs the read-only RCA/feature/query agent and records the
result as an `agent` message on the thread, then advances the request status.
It is enqueued when a request is created and on every admin reply.
"""
from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@shared_task(bind=True, max_retries=0)
def process_request(self, request_id):
    from debugger.agent import run_agent
    from debugger.models import DebugMessage, DebugRequest

    request = DebugRequest.objects.filter(id=request_id).first()
    if not request:
        logger.warning('process_request: request %s not found', request_id)
        return

    # Don't re-run terminal threads.
    if request.status in (DebugRequest.Status.CLOSED,):
        return

    request.status = DebugRequest.Status.ANALYZING
    request.error = ''
    request.save(update_fields=['status', 'error', 'updated_at'])

    try:
        result = run_agent(request)
    except Exception as exc:  # SDK/CLI missing, API error, timeout, etc.
        logger.exception('process_request failed for %s', request_id)
        request.status = DebugRequest.Status.FAILED
        request.error = str(exc)
        request.save(update_fields=['status', 'error', 'updated_at'])
        DebugMessage.objects.create(
            request=request, role=DebugMessage.Role.SYSTEM,
            content=f'Analysis failed: {exc}',
        )
        return

    text = result.get('text') or '(the agent returned no text)'
    proposals = result.get('proposals') or []

    DebugMessage.objects.create(
        request=request, role=DebugMessage.Role.AGENT, content=text,
        meta={
            'tools_used': result.get('tools_used'),
            'usage': result.get('usage'),
            'has_proposal': bool(proposals),
        },
    )

    update_fields = ['status', 'updated_at']

    # Bug/feature: keep the latest analysis as the RCA/plan shown in the panel.
    if request.kind in (DebugRequest.Kind.BUG, DebugRequest.Kind.FEATURE):
        request.rca = text
        update_fields.append('rca')

    if proposals:
        # Take the last proposal as the current suggested fix.
        p = proposals[-1]
        request.proposed_diff = p.get('diff', '')
        request.pr_title = (p.get('pr_title', '') or request.title)[:200]
        request.pr_body = p.get('pr_body', '')
        request.status = DebugRequest.Status.READY
        update_fields += ['proposed_diff', 'pr_title', 'pr_body']
    else:
        # Answered — the ball is back with the admin (ask follow-up or close).
        request.status = DebugRequest.Status.AWAITING_INPUT

    request.save(update_fields=list(set(update_fields)))
    return {'request_id': request_id, 'status': request.status}
