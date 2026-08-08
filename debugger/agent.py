"""
The RCA / feature / query engine — a locked-down Claude Agent SDK run.

Capabilities given to Claude:
  * Read / Grep / Glob on the code checkout (read-only).
  * Bash, but only the read-only allowlist in guards.py (a PreToolUse hook
    denies everything else, including any git push).
  * db_query_ro  — SELECT-only SQL via the `readonly` DB alias (dedicated PG role).
  * read_logs    — journalctl (app) + nginx logfiles, read-only.

It can NEVER Write/Edit files or write to the DB. Those guarantees are enforced
three ways: the allowlist of tools, the PreToolUse deny-hook, and (for the DB)
the Postgres read-only role itself.

Phase 1 is analysis only — `propose_fix` records a diff/PR text as a suggestion
but performs no git or GitHub side effects (Phase 2 wires the PR path).
"""
import asyncio
import json
import subprocess

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db import connections

from debugger import guards

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


MCP_SERVER_NAME = 'varthaai_debugger'
DB_TOOL = f'mcp__{MCP_SERVER_NAME}__db_query_ro'
LOGS_TOOL = f'mcp__{MCP_SERVER_NAME}__read_logs'
FIX_TOOL = f'mcp__{MCP_SERVER_NAME}__propose_fix'
LEARN_TOOL = f'mcp__{MCP_SERVER_NAME}__record_learning'

ALLOWED_TOOLS = ['Read', 'Grep', 'Glob', 'Bash', DB_TOOL, LOGS_TOOL, FIX_TOOL, LEARN_TOOL]
DISALLOWED_TOOLS = list(guards.BLOCKED_TOOLS) + ['WebFetch', 'WebSearch', 'TodoWrite']


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
                         'a PR or write any files.',
          {'diff': str, 'pr_title': str, 'pr_body': str})
    async def propose_fix(args):
        args = args or {}
        proposals.append({
            'diff': args.get('diff', ''),
            'pr_title': args.get('pr_title', ''),
            'pr_body': args.get('pr_body', ''),
        })
        return _text('Fix proposal recorded. It will be shown to the admin with '
                     'a "Create PR" button; no PR has been opened.')

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

    return create_sdk_mcp_server(
        name=MCP_SERVER_NAME, version='1.0.0',
        tools=[db_query_ro, read_logs, propose_fix, record_learning],
    )


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
            '(3) server logs via read_logs. State the root cause explicitly, '
            'cite the exact files/lines and the log/DB evidence. If you are '
            'confident in a fix, call propose_fix with a unified diff and a PR '
            'title/body. If you need more information from the admin, ask a '
            'specific question and stop.'
        ),
        'feature': (
            'This is a FEATURE request. First ask any clarifying questions you '
            'need (stop and wait for answers). Once requirements are clear, '
            'produce a concrete implementation plan referencing the real files '
            'to change. Do not propose a diff until the admin approves the plan.'
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
            'diff against main). If a comment is a question or you disagree, '
            'explain your reasoning and ask the admin — do NOT propose a diff in '
            'that case.'
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
        "- Be concise and structured. Use short headings.\n\n"
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
        max_turns=40,
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
        elif isinstance(message, ResultMessage):
            usage = {
                'total_cost_usd': getattr(message, 'total_cost_usd', None),
                'duration_ms': getattr(message, 'duration_ms', None),
                'num_turns': getattr(message, 'num_turns', None),
            }

    return {
        'text': '\n\n'.join(t for t in text_parts if t.strip()).strip(),
        'proposals': proposals,
        'learnings': learnings,
        'tools_used': tools_used,
        'usage': usage,
    }
