"""
The RCA / feature / query engine — a locked-down headless `claude` CLI run
(`claude -p ...`, via core.claude_cli.run_claude_cli). Previously this ran the
Claude Agent SDK in-process; it now shells out to the CLI directly (see
core/claude_cli.py's docstring for why — a Redis-backed concurrency limit
shared with other agentic-Claude apps, and one less Python dependency to pin).

Capabilities given to Claude:
  * Read / Grep / Glob on the code checkout (read-only).
  * Bash, but only the read-only allowlist in guards.py (a PreToolUse hook —
    now a standalone script, debugger/guard_hook.py, registered via the CLI's
    --settings flag — denies everything else, including any git push).
  * db_query_ro  — SELECT-only SQL via the `readonly` DB alias (dedicated PG
    role), served by debugger/mcp_server.py over stdio (a separate OS process
    the CLI spawns via --mcp-config; see _build_mcp_config below).
  * read_logs    — journalctl (app) + nginx logfiles, read-only.
  * consult_advisor — escalates final synthesis (root cause / fix diff / plan)
    to a stronger model (settings.DEBUGGER_ADVISOR_MODEL) via a SECOND headless
    `claude -p` turn (run_claude_cli again, with an empty tool allowlist —
    NOT the direct `anthropic` SDK, which no service in this codebase uses
    any more; see core/claude_cli.py), so the advisor still gets no tools of
    its own, only the text summary it's given (_call_advisor below, called
    from inside debugger/mcp_server.py's consult_advisor tool — itself a
    child process of THIS outer `claude` run, which already holds one
    concurrency slot; the advisor call needs a second slot concurrently, so
    CLAUDE_CLI_MAX_CONCURRENT must stay >= 2 or this self-deadlocks).

It can NEVER Write/Edit files or write to the DB. Those guarantees are enforced
three ways: the CLI allow/deny tool lists, the PreToolUse deny-hook script,
and (for the DB) the Postgres read-only role itself.

Phase 1 is analysis only — `propose_fix` records a diff/PR text as a suggestion
but performs no git or GitHub side effects (Phase 2 wires the PR path, see
debugger/pr.py — the agent itself never runs git/GitHub commands).
"""
import logging
import os
import shutil
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank

from core.claude_cli import NO_TOOLS, run_claude_cli

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = 'varthaai_debugger'
DB_TOOL = f'mcp__{MCP_SERVER_NAME}__db_query_ro'
LOGS_TOOL = f'mcp__{MCP_SERVER_NAME}__read_logs'
FIX_TOOL = f'mcp__{MCP_SERVER_NAME}__propose_fix'
LEARN_TOOL = f'mcp__{MCP_SERVER_NAME}__record_learning'
ADVISOR_TOOL = f'mcp__{MCP_SERVER_NAME}__consult_advisor'

ALLOWED_TOOLS = [
    'Read', 'Grep', 'Glob', 'Bash', DB_TOOL, LOGS_TOOL, FIX_TOOL, LEARN_TOOL, ADVISOR_TOOL,
]
# Names as recognized by the current `claude` CLI build (verified empirically —
# "MultiEdit" / "NotebookWrite" are not registered tool names in this CLI
# version and produce a harmless-but-noisy "matches no known tool" warning if
# included). debugger/guards.py's BLOCKED_TOOLS is the authoritative denylist
# used by the PreToolUse hook and deliberately keeps the wider set (belt and
# braces — if the CLI ever reintroduces those names, the hook still blocks
# them even though they're not listed here).
DISALLOWED_TOOLS = ['Write', 'Edit', 'NotebookEdit', 'WebFetch', 'WebSearch', 'TodoWrite']


# --------------------------------------------------------------------------- #
# Shared helpers (also used by debugger/mcp_server.py, which runs as a        #
# separate OS process and imports these directly)                             #
# --------------------------------------------------------------------------- #
def _extract_new_files(raw_list):
    """Validate/normalize an LLM-supplied `new_files` list into
    [{'path': str, 'content': str}, ...], dropping malformed entries. Shared
    by the propose_fix MCP tool (debugger/mcp_server.py, for its
    acknowledgement message) and by run_agent's proposal recovery below, so
    both views of "how many new files were proposed" agree."""
    out = []
    for item in (raw_list or []):
        if not isinstance(item, dict):
            continue
        path = (item.get('path') or '').strip()
        content = item.get('content')
        if path and content is not None:
            out.append({'path': path, 'content': content})
    return out


def _call_advisor(summary):
    """
    Synchronous call to the advisor model — a second headless `claude -p`
    turn via run_claude_cli, with an empty tool allowlist (NO_TOOLS), NOT the
    direct `anthropic` SDK: the advisor still gets no tool/filesystem/DB
    access of its own, only the text it's given; only the transport and auth
    changed (see core/claude_cli.py — no service here uses ANTHROPIC_API_KEY).
    Returns the advisor's text, or raises on failure. Called from
    debugger/mcp_server.py's consult_advisor tool (a separate OS process —
    that's why this is a plain, self-contained function rather than a closure
    over any in-process state).

    This call is itself nested inside an OUTER `claude` run that already
    holds one concurrency slot (the investigation calling consult_advisor) —
    it needs a SECOND slot concurrently. See module docstring: requires
    CLAUDE_CLI_MAX_CONCURRENT >= 2.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')
    result = run_claude_cli(
        summary,
        disallowed_tools=NO_TOOLS,
        model=settings.DEBUGGER_ADVISOR_MODEL,
        max_turns=1,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'advisor call failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = (result.get('text') or '').strip()
    if not text:
        raise RuntimeError('advisor returned no text')
    return text


# --------------------------------------------------------------------------- #
# CLI wiring: MCP server + guard hook                                         #
# --------------------------------------------------------------------------- #
def _build_mcp_config():
    """
    MCP server config for --mcp-config: spawns `manage.py run_debugger_mcp`
    (debugger/mcp_server.py) as a stdio child of the `claude` CLI process.
    Uses absolute paths for both the interpreter and manage.py so this works
    regardless of the CLI subprocess's cwd (which is DEBUGGER_CODE_DIR in the
    normal case, or a PR-branch worktree checkout in review mode — Python
    inserts a script's own directory onto sys.path when it's run directly, so
    manage.py resolves the project's packages either way).
    """
    return {
        'mcpServers': {
            MCP_SERVER_NAME: {
                'type': 'stdio',
                'command': sys.executable,
                'args': [str(Path(settings.BASE_DIR) / 'manage.py'), 'run_debugger_mcp'],
                'env': {
                    'DJANGO_SETTINGS_MODULE': os.environ.get(
                        'DJANGO_SETTINGS_MODULE', 'Varthaai.settings'),
                },
            }
        }
    }


def _build_hook_settings():
    """
    --settings payload registering debugger/guard_hook.py as a PreToolUse
    hook for every tool call — the CLI equivalent of the Agent SDK's
    HookMatcher. Verified empirically that this still fires (and can deny)
    even under --dangerously-skip-permissions, and that --setting-sources ''
    does not suppress an explicitly-passed --settings hook.
    """
    hook_script = Path(__file__).resolve().parent / 'guard_hook.py'
    return {
        'hooks': {
            'PreToolUse': [
                {'matcher': '*', 'hooks': [
                    {'type': 'command', 'command': f'{sys.executable} {hook_script}'}
                ]}
            ]
        }
    }


# --------------------------------------------------------------------------- #
# Prompt building                                                             #
# --------------------------------------------------------------------------- #
def build_system_prompt(request, mode=None):
    learnings_txt = _relevant_learnings(request)

    claude_md = _read_project_map()

    kind_instructions = {
        'query': (
            'This is a QUERY. Answer the question directly and concisely using '
            'the code, a read-only DB SELECT, and/or logs as needed. Do NOT '
            'propose a fix or a PR unless explicitly asked.'
        ),
        'bug': (
            'This is a BUG. Do a rigorous root-cause analysis grounded in THREE '
            'sources: (1) the code, (2) read-only DB SELECTs via db_query_ro, '
            '(3) server logs via read_logs. Once you have gathered enough '
            'evidence and are ready to state the root cause or draft a fix, '
            'call consult_advisor with a full summary of that evidence and your '
            'hypothesis, then write your final root-cause statement and any '
            'propose_fix diff informed by its response. Cite the exact '
            'files/lines and the log/DB evidence. If you need more information '
            'from the admin instead, ask a specific question and stop (skip '
            'consult_advisor in that case).'
        ),
        'feature': (
            'This is a FEATURE request. First ask any clarifying questions you '
            'need (stop and wait for answers). Once requirements are clear, '
            'call consult_advisor with the requirements and the real files/'
            'models involved, then produce a concrete implementation plan '
            '(informed by its response) referencing the real files to change. '
            'Do not propose a diff until the admin approves the plan.'
        ),
    }[request.kind]

    if mode == 'finalize':
        kind_instructions = (
            'This thread is being CLOSED. Distil ONE reusable debugging '
            'HEURISTIC from it -- a generic rule that helps diagnose a '
            'DIFFERENT future bug/feature of the same CLASS, not a record of '
            'this incident. Call record_learning(title, content).\n'
            'Rules: (1) Do NOT narrate what happened here -- no "Issue: X was '
            'broken because Y", no specific request/thread details; abstract '
            'the pattern instead (e.g. "symptom class -> check X before '
            'assuming Y"). (2) content must be 2-3 sentences MAX, phrased as '
            'a rule a future agent can apply cold to an unrelated thread. '
            '(3) title is a short generic label for the bug/feature CLASS '
            '(e.g. "API envelope unwrap mismatch"), not a summary of this '
            'thread. If nothing here generalizes beyond this one thread, say '
            'so briefly and do not call record_learning.'
        )

    if mode == 'review':
        # The cwd is the PR branch worktree, not main — the code already has the
        # previously proposed fix applied. Produce a DELTA on top of it.
        kind_instructions = (
            'A pull request is already open for this thread and a reviewer has '
            'left feedback (see the SYSTEM "PR review" entries in the '
            'conversation). The code you are reading here is the PR BRANCH — it '
            'ALREADY contains your earlier fix. Address the review comments. If '
            'code changes are needed, call propose_fix with a unified diff that '
            'applies ON TOP of the current branch state (a delta, not a fresh '
            'diff against main); any additional brand-new file still goes in '
            'new_files as full content, not a diff hunk. If a comment is a '
            'question or you disagree, explain your reasoning and ask the '
            'admin — do NOT propose a diff in that case.'
        )

    return (
        "You are the Varthaai Debugger Agent — a senior engineer embedded in the "
        "admin dashboard of a Django app (Varthaai, a food brand with B2B + B2C "
        "sales). You investigate bugs, scope features, and answer queries.\n\n"
        "STRICT RULES:\n"
        "- You are READ-ONLY. You may read code, run SELECT-only SQL via "
        "db_query_ro, and read logs via read_logs. You may run only read-only "
        "shell commands. You cannot and must not write files, write to the DB, "
        "or push to git. Do not attempt blocked actions.\n"
        "- Ground every claim in evidence. Prefer citing file:line, a query "
        "result, or a log excerpt over speculation.\n"
        "- Be concise and structured. Use short headings.\n"
        "- consult_advisor is a stronger model with NO tool access of its own — "
        "it only sees the summary text you give it. Use it once, right before "
        "finalizing, not as a substitute for your own investigation.\n"
        "- When calling propose_fix: put edits to EXISTING files in `diff` as "
        "normal unified-diff hunks. Put any BRAND-NEW file in `new_files` as "
        "full content, NOT as a `@@ -0,0 +1,N @@` diff hunk — getting N exactly "
        "right for a large new file is unreliable and a single wrong count "
        "corrupts the whole patch, which breaks PR creation.\n\n"
        f"{kind_instructions}\n\n"
        "PROJECT MAP:\n"
        f"{claude_md}\n\n"
        "PRIOR LEARNINGS (your memory from past threads — apply them):\n"
        f"{learnings_txt}\n"
    )


def _relevant_learnings(request, limit=8):
    """
    The agent's memory: relevant past learnings, retrieved via Postgres full-
    text search ranked against this request's title+body (RAG over learnings,
    not a recency/keyword-overlap dump of everything). Falls back to recent
    same-kind learnings on a cold start / no lexical match.
    """
    from debugger.models import DebugLearning

    query_text = f'{request.title} {request.body}'.strip()
    top = []
    if query_text:
        sq = SearchQuery(query_text, search_type='websearch')
        top = list(
            DebugLearning.objects
            .annotate(rank=SearchRank('search_vector', sq))
            .filter(rank__gt=0)
            .order_by('-rank')[:limit]
        )
    if not top:
        top = list(
            DebugLearning.objects.filter(kind=request.kind).order_by('-created_at')[:5]
        )
    if not top:
        return '(no learnings recorded yet)'
    return '\n'.join(f'- ({l.kind}) {l.title}: {l.content}' for l in top)


def _read_project_map():
    try:
        p = Path(settings.DEBUGGER_CODE_DIR) / 'CLAUDE.md'
        text = p.read_text(encoding='utf-8')
        # Keep the prompt bounded.
        return text[:8000]
    except Exception:
        return '(CLAUDE.md not available)'


def build_prompt(request):
    """The user turn = the request + full chat thread so far."""
    lines = [f'REQUEST ({request.kind}): {request.title}', '']
    if request.body:
        lines += [request.body, '']
    thread = request.messages.all().order_by('created_at', 'id')
    if thread:
        lines.append('--- CONVERSATION SO FAR ---')
        for m in thread:
            who = {'admin': 'ADMIN', 'agent': 'YOU (agent)', 'system': 'SYSTEM'}.get(m.role, m.role)
            lines.append(f'{who}: {m.content}')
    return '\n'.join(lines)


# --------------------------------------------------------------------------- #
# Runner                                                                       #
# --------------------------------------------------------------------------- #
def run_agent(request, cwd=None, mode=None):
    """
    Synchronous entry point (called from the Celery task). Returns a dict:
      {text, proposals: [...], learnings: [...], tools_used: [...], usage: {...}}
    `cwd` overrides the code checkout (used for review mode, where the agent
    reads the PR-branch worktree). `mode='review'` switches the prompt.
    Raises RuntimeError if the `claude` CLI is unavailable or the run reports
    an error result (including a likely max-turns truncation — see below).
    core.claude_cli.ClaudeCliBusyError propagates if no concurrency slot frees
    up in time (subclasses RuntimeError, so existing exception handling in
    debugger/tasks.py still catches it as a generic failure).
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    # build_system_prompt / build_prompt touch the ORM — safe here since,
    # unlike the old SDK path, run_agent is a plain sync function (no asyncio
    # event loop), so there's no Django sync-DB-access-in-async-context issue.
    system_prompt = build_system_prompt(request, mode)
    user_prompt = build_prompt(request)

    result = run_claude_cli(
        user_prompt,
        system_prompt=system_prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        mcp_config=_build_mcp_config(),
        extra_settings=_build_hook_settings(),
        model=settings.DEBUGGER_MODEL,
        cwd=cwd or settings.DEBUGGER_CODE_DIR,
        max_turns=settings.DEBUGGER_MAX_TURNS,
    )

    proposals = []
    learnings = []
    tools_used = []
    for call in result['tool_calls']:
        name = call.get('name')
        tools_used.append(name)
        payload = call.get('input') or {}
        if name == FIX_TOOL:
            proposals.append({
                'diff': payload.get('diff', ''),
                'new_files': _extract_new_files(payload.get('new_files')),
                'pr_title': payload.get('pr_title', ''),
                'pr_body': payload.get('pr_body', ''),
            })
        elif name == LEARN_TOOL:
            learnings.append({
                'title': (payload.get('title', '') or '')[:200],
                'content': payload.get('content', '') or '',
            })
        # Logged as it happens (not at the end) so a mid-run kill (e.g.
        # SoftTimeLimitExceeded) still leaves a trail of how far the
        # investigation got.
        logger.info('request %s: tool call #%d: %s', request.id, len(tools_used), name)

    usage = {
        'total_cost_usd': result.get('total_cost_usd'),
        'duration_ms': result.get('duration_ms'),
        'num_turns': result.get('num_turns'),
    }
    # Logged here (not just returned) because on an error result the caller
    # (tasks.py) only surfaces a short message to the admin — duration/turn
    # count would otherwise only survive in a failed run's traceback.
    logger.info(
        'request %s: result usage num_turns=%s duration_ms=%s is_error=%s subtype=%s',
        request.id, usage['num_turns'], usage['duration_ms'],
        result.get('is_error'), result.get('subtype'))

    # The CLI's maxTurns setting stops the run WITHOUT flagging it as an error
    # result (is_error=False, subtype='success') — unlike the old Agent SDK,
    # which raised a distinct exception on hitting max_turns. We approximate
    # that same signal by checking whether the run used exactly (or more
    # than) the configured cap, and raise with the same substring
    # ('maximum number of turns') that debugger/tasks.py's _is_max_turns_error
    # already looks for, so the existing admin-facing message ("try breaking
    # it into smaller... questions") keeps working unchanged. This can, in
    # principle, false-positive if the agent genuinely finishes exactly on
    # the turn boundary — a known, documented tradeoff of the CLI not
    # distinguishing "done" from "cut off" the way the SDK's exception did.
    if result.get('num_turns') and result['num_turns'] >= settings.DEBUGGER_MAX_TURNS:
        raise RuntimeError(
            'Claude Code returned an error result: Reached maximum number of '
            f'turns ({settings.DEBUGGER_MAX_TURNS})')

    if result.get('is_error'):
        raise RuntimeError(
            f'claude CLI reported an error result (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')

    return {
        'text': result.get('text') or '',
        'proposals': proposals,
        'learnings': learnings,
        'tools_used': tools_used,
        'usage': usage,
    }
