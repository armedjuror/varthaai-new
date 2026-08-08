"""
The RCA / feature / query engine — a locked-down Claude Agent SDK run.

Capabilities given to Claude:
  * Read / Grep / Glob on the code checkout (read-only).
  * Bash, but only the read-only allowlist in guards.py (a PreToolUse hook
    denies everything else, including any git push).
  * db_query_ro  — SELECT-only SQL via the `readonly` DB alias (dedicated PG role).
  * read_logs    — journalctl (app) + nginx logfiles, read-only.
  * consult_advisor — escalates final synthesis (root cause / fix diff / plan)
    to a stronger model (settings.DEBUGGER_ADVISOR_MODEL) via a direct
    Anthropic API call, NOT the Agent SDK — the advisor gets no tools of its
    own, only the text summary it's given.

It can NEVER Write/Edit files or write to the DB. Those guarantees are enforced
three ways: the allowlist of tools, the PreToolUse deny-hook, and (for the DB)
the Postgres read-only role itself.

Phase 1 is analysis only — `propose_fix` records a diff/PR text as a suggestion
but performs no git or GitHub side effects (Phase 2 wires the PR path).
"""
import asyncio
import json
import logging
import subprocess

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connections

from debugger import guards

logger = logging.getLogger(__name__)

# The SDK spawns the `claude` CLI (Node). Import lazily/guarded so the rest of
# the app (models, migrations, views) loads even where the SDK/CLI is absent.
try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        create_sdk_mcp_server,
        query,
        tool,
    )
    SDK_AVAILABLE = True
    SDK_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - depends on deploy env
    SDK_AVAILABLE = False
    SDK_IMPORT_ERROR = exc

# Direct Anthropic API client for the advisor tool — deliberately NOT the
# Agent SDK, so the advisor model gets no tool/filesystem/DB access of its
# own (text in, text out). Import lazily for the same reason as the SDK above.
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except Exception:  # pragma: no cover - depends on deploy env
    ANTHROPIC_AVAILABLE = False


MCP_SERVER_NAME = 'varthaai_debugger'
DB_TOOL = f'mcp__{MCP_SERVER_NAME}__db_query_ro'
LOGS_TOOL = f'mcp__{MCP_SERVER_NAME}__read_logs'
FIX_TOOL = f'mcp__{MCP_SERVER_NAME}__propose_fix'
LEARN_TOOL = f'mcp__{MCP_SERVER_NAME}__record_learning'
ADVISOR_TOOL = f'mcp__{MCP_SERVER_NAME}__consult_advisor'

ALLOWED_TOOLS = [
    'Read', 'Grep', 'Glob', 'Bash', DB_TOOL, LOGS_TOOL, FIX_TOOL, LEARN_TOOL, ADVISOR_TOOL,
]
DISALLOWED_TOOLS = list(guards.BLOCKED_TOOLS) + ['WebFetch', 'WebSearch', 'TodoWrite']

# Full JSON Schema (not the {name: type} shorthand) so new_files can be an
# array of {path, content} objects — see propose_fix below for why new files
# are kept out of the diff entirely.
FIX_TOOL_SCHEMA = {
    'type': 'object',
    'properties': {
        'diff': {
            'type': 'string',
            'description': 'Unified diff for changes to EXISTING files only '
                           '(modifications/deletions). Do not add hunks that '
                           'create new files here — use new_files for those. '
                           'May be empty if the fix is only new files.',
        },
        'new_files': {
            'type': 'array',
            'description': 'Brand-new files this fix adds, as full file '
                           'content (not a diff hunk). May be omitted/empty '
                           'if the fix only modifies existing files.',
            'items': {
                'type': 'object',
                'properties': {
                    'path': {'type': 'string', 'description': "Repo-relative path, e.g. 'marketing/ai.py'."},
                    'content': {'type': 'string', 'description': 'The full content of the new file.'},
                },
                'required': ['path', 'content'],
            },
        },
        'pr_title': {'type': 'string'},
        'pr_body': {'type': 'string'},
    },
    'required': ['diff', 'pr_title', 'pr_body'],
}


# --------------------------------------------------------------------------- #
# Custom read-only tools                                                       #
# --------------------------------------------------------------------------- #
def _text(s):
    return {'content': [{'type': 'text', 'text': s}]}


def _error(s):
    return {'content': [{'type': 'text', 'text': s}], 'is_error': True}


def _register_tools(proposals, learnings=None):
    """
    Build the SDK tool set. `proposals` collects propose_fix calls; `learnings`
    (optional list) collects record_learning calls.
    """
    if learnings is None:
        learnings = []

    @tool('db_query_ro', 'Run ONE read-only SQL SELECT against the production '
                         'database (SELECT/WITH only). Returns rows as JSON.',
          {'sql': str})
    async def db_query_ro(args):
        sql = (args or {}).get('sql', '')
        ok, reason = guards.check_sql_readonly(sql)
        if not ok:
            return _error(f'Rejected: {reason}')
        limit = settings.DEBUGGER_DB_ROW_LIMIT
        try:
            # Run in a thread — Django DB access is sync.
            def _run():
                with connections['readonly'].cursor() as cur:
                    cur.execute(sql)
                    cols = [c[0] for c in cur.description] if cur.description else []
                    rows = cur.fetchmany(limit + 1)
                    return cols, rows
            cols, rows = await asyncio.to_thread(_run)
        except Exception as exc:
            return _error(f'Query error: {exc}')
        truncated = len(rows) > limit
        rows = rows[:limit]
        payload = {
            'columns': cols,
            'row_count': len(rows),
            'truncated': truncated,
            'rows': [
                {c: _jsonable(v) for c, v in zip(cols, r)} for r in rows
            ],
        }
        return _text(json.dumps(payload, default=str, indent=2))

    @tool('read_logs', 'Read server logs (read-only). source is one of '
                       '"app" (journalctl -u the app unit), "nginx_access", '
                       '"nginx_error". Optional grep filters lines; lines caps '
                       'how many recent lines to return.',
          {'source': str, 'grep': str, 'lines': int})
    async def read_logs(args):
        args = args or {}
        source = args.get('source', 'app')
        grep = (args.get('grep') or '').strip()
        lines = int(args.get('lines') or 300)
        lines = max(1, min(lines, 2000))
        try:
            text = await asyncio.to_thread(_read_log_source, source, lines)
        except Exception as exc:
            return _error(f'Log read error: {exc}')
        if grep:
            kept = [ln for ln in text.splitlines() if grep.lower() in ln.lower()]
            text = '\n'.join(kept[-lines:]) or '(no matching lines)'
        return _text(text or '(empty)')

    @tool('propose_fix', 'Record a suggested fix as a unified diff plus PR title '
                         'and body. Records the proposal only — it does NOT open '
                         'a PR or write any files. IMPORTANT: put changes to '
                         'EXISTING files in `diff` (as normal unified-diff '
                         'hunks). Put brand-new files in `new_files` as full '
                         'content instead of a diff hunk — hand-counting lines '
                         'in a `@@ -0,0 +1,N @@` hunk for a large new file is '
                         'exactly the kind of arithmetic mistake that corrupts '
                         'the whole patch and breaks PR creation.',
          FIX_TOOL_SCHEMA)
    async def propose_fix(args):
        args = args or {}
        new_files = []
        for item in (args.get('new_files') or []):
            if not isinstance(item, dict):
                continue
            path = (item.get('path') or '').strip()
            content = item.get('content')
            if path and content is not None:
                new_files.append({'path': path, 'content': content})
        proposals.append({
            'diff': args.get('diff', ''),
            'new_files': new_files,
            'pr_title': args.get('pr_title', ''),
            'pr_body': args.get('pr_body', ''),
        })
        note = f' + {len(new_files)} new file(s) attached as full content' if new_files else ''
        return _text(f'Fix proposal recorded{note}. It will be shown to the admin '
                     'with a "Create PR" button; no PR has been opened.')

    @tool('record_learning', 'Record ONE reusable lesson from this thread as a '
                             'title + content. Used when finalizing a thread on '
                             'close so future investigations benefit. Records '
                             'only — the admin reviews it before it is saved.',
          {'title': str, 'content': str})
    async def record_learning(args):
        args = args or {}
        learnings.append({
            'title': (args.get('title', '') or '')[:200],
            'content': args.get('content', '') or '',
        })
        return _text('Learning drafted. The admin will review and edit it before '
                     'it is saved to memory.')

    @tool('consult_advisor', 'Escalate to a stronger model for final synthesis '
                             '— stating the root cause, drafting a fix diff, or '
                             'writing a feature plan. Call this ONCE you have '
                             'gathered enough evidence and are ready to produce '
                             'the final answer, not while still exploring. The '
                             'advisor has NO tool access — pass it everything it '
                             'needs to judge (file:line excerpts, DB query '
                             'results, log excerpts, your hypothesis) in the '
                             'summary; it only sees what you write here.',
          {'summary': str})
    async def consult_advisor(args):
        summary = (args or {}).get('summary', '').strip()
        if not summary:
            return _error('Empty summary — nothing to advise on.')
        try:
            text = await asyncio.to_thread(_call_advisor, summary)
        except Exception as exc:
            logger.warning('consult_advisor call failed: %s', exc)
            return _error(f'Advisor call failed: {exc}')
        return _text(text or '(advisor returned no text)')

    return create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version='1.0.0',
        tools=[db_query_ro, read_logs, propose_fix, record_learning, consult_advisor],
    )


def _call_advisor(summary):
    """
    Synchronous call to the advisor model (a direct Anthropic API call, NOT the
    Agent SDK — the advisor gets no tool/filesystem/DB access of its own, only
    the text it's given). Returns the advisor's text, or raises on failure.
    """
    if not ANTHROPIC_AVAILABLE:
        raise RuntimeError('anthropic package not installed')
    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError('ANTHROPIC_API_KEY not configured')
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=settings.DEBUGGER_ADVISOR_MODEL,
        max_tokens=4096,
        thinking={'type': 'adaptive'},
        messages=[{'role': 'user', 'content': summary}],
    )
    if response.stop_reason == 'refusal':
        raise RuntimeError('advisor declined to respond')
    return '\n\n'.join(
        b.text for b in response.content if getattr(b, 'type', None) == 'text')


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def _read_log_source(source, lines):
    if source == 'app':
        unit = settings.DEBUGGER_LOG_UNIT
        out = subprocess.run(
            ['journalctl', '-u', unit, '--no-pager', '-n', str(lines)],
            capture_output=True, text=True, timeout=30,
        )
        return out.stdout or out.stderr
    path = {
        'nginx_access': settings.DEBUGGER_NGINX_ACCESS_LOG,
        'nginx_error': settings.DEBUGGER_NGINX_ERROR_LOG,
    }.get(source)
    if not path:
        return f'Unknown log source: {source}'
    out = subprocess.run(
        ['tail', '-n', str(lines), path],
        capture_output=True, text=True, timeout=30,
    )
    return out.stdout or out.stderr


# --------------------------------------------------------------------------- #
# Guardrail hook                                                               #
# --------------------------------------------------------------------------- #
async def _pretooluse_hook(input_data, tool_use_id, context):
    tool_name = input_data.get('tool_name', '')
    tool_input = input_data.get('tool_input', {})
    decision, reason = guards.evaluate_tool(tool_name, tool_input)
    if decision == 'deny':
        return {
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }
        }
    return {}


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
        from pathlib import Path
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
      {text, proposals: [...], usage: {...}, tools_used: [...]}
    `cwd` overrides the code checkout (used for review mode, where the agent
    reads the PR-branch worktree). `mode='review'` switches the prompt.
    Raises RuntimeError if the SDK/CLI is unavailable.
    """
    if not SDK_AVAILABLE:
        raise RuntimeError(f'claude-agent-sdk unavailable: {SDK_IMPORT_ERROR}')
    return asyncio.run(_run_agent_async(request, cwd=cwd, mode=mode))


async def _run_agent_async(request, cwd=None, mode=None):
    proposals = []
    learnings = []
    server = _register_tools(proposals, learnings)

    env = {}
    if settings.ANTHROPIC_API_KEY:
        env['ANTHROPIC_API_KEY'] = settings.ANTHROPIC_API_KEY

    # build_system_prompt / build_prompt touch the ORM; Django forbids sync DB
    # access from inside a running event loop, so run them in a worker thread.
    system_prompt = await asyncio.to_thread(build_system_prompt, request, mode)
    user_prompt = await asyncio.to_thread(build_prompt, request)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=ALLOWED_TOOLS,
        disallowed_tools=DISALLOWED_TOOLS,
        mcp_servers={MCP_SERVER_NAME: server},
        hooks={'PreToolUse': [HookMatcher(matcher='*', hooks=[_pretooluse_hook])]},
        permission_mode='default',
        cwd=cwd or settings.DEBUGGER_CODE_DIR,
        model=settings.DEBUGGER_MODEL,
        max_turns=settings.DEBUGGER_MAX_TURNS,
        setting_sources=[],   # hermetic: ignore ~/.claude and project settings
        env=env,
    )

    text_parts = []
    tools_used = []
    usage = {}

    async for message in query(prompt=user_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tools_used.append(block.name)
                    # Logged as it happens (not at the end) so a mid-run kill
                    # (e.g. SoftTimeLimitExceeded) still leaves a trail of how
                    # far the investigation got.
                    logger.info(
                        'request %s: tool call #%d: %s',
                        request.id, len(tools_used), block.name)
        elif isinstance(message, ResultMessage):
            usage = {
                'total_cost_usd': getattr(message, 'total_cost_usd', None),
                'duration_ms': getattr(message, 'duration_ms', None),
                'num_turns': getattr(message, 'num_turns', None),
            }
            # Logged here (not just returned) because on an error result (e.g.
            # max-turns) the CLI still emits this message before the SDK raises
            # — so this is the only place duration/turn count survive a failed
            # run. Distinguishes "40 turns in 30s" (looping) from "40 turns in
            # 900s" (genuinely heavy work) for the next occurrence.
            logger.info(
                'request %s: result usage num_turns=%s duration_ms=%s is_error=%s',
                request.id, usage['num_turns'], usage['duration_ms'],
                getattr(message, 'is_error', None))

    return {
        'text': '\n\n'.join(t for t in text_parts if t.strip()).strip(),
        'proposals': proposals,
        'learnings': learnings,
        'tools_used': tools_used,
        'usage': usage,
    }
