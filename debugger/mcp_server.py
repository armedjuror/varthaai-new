"""
The Debugger Agent's custom MCP tool server — run as a separate `claude` CLI
child process over stdio (wired in via `--mcp-config`; see debugger/agent.py's
_build_mcp_config and debugger/management/commands/run_debugger_mcp.py, which
is the actual entry point `claude` spawns).

This replaces the Claude Agent SDK's `create_sdk_mcp_server` (in-process
Python tool functions). The tools themselves are unchanged in behavior:

  * db_query_ro     — SELECT-only SQL via the `readonly` DB alias (a dedicated
                       read-only Postgres role — see deploy/DEBUGGER.md).
  * read_logs        — journalctl (app) + nginx logs, read-only.
  * propose_fix       — records a diff/PR text as a suggestion. Unlike the SDK
                       version, this does NOT need to append to a shared
                       Python list (that list lived in the SAME process as the
                       SDK's query() loop; this tool runs in a different OS
                       process from the caller). Its return value is purely an
                       acknowledgement string shown back to the agent — the
                       actual diff/pr_title/pr_body/new_files are recovered by
                       the CALLER (debugger/agent.py) by reading the raw
                       `input` of this tool's tool_use block out of the
                       `claude` CLI's --output-format stream-json event
                       stream, which is available across the process
                       boundary (see core.claude_cli._parse_stream_json).
  * record_learning    — same story as propose_fix: acknowledgement only, the
                       real title/content is recovered from the tool_use input.
  * consult_advisor    — a SECOND headless `claude -p` call with no tools
                       granted (debugger.agent._call_advisor, via
                       core.claude_cli.run_claude_cli) — the advisor model
                       still gets no tool/filesystem/DB access of its own,
                       only the text it's given; it is nested inside THIS
                       already-running `claude` process, so it needs its own
                       concurrency slot (CLAUDE_CLI_MAX_CONCURRENT >= 2).

It can NEVER write files or write to the DB — see debugger/guards.py (enforced
independently by the PreToolUse hook script, debugger/guard_hook.py) and the
Postgres read-only role for the actual guarantees; this module has no write
capability at all, guarded or not.
"""
import asyncio
import json
import subprocess

import django

django.setup()

from django.conf import settings  # noqa: E402
from django.db import connections  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from debugger import guards  # noqa: E402
from debugger.agent import MCP_SERVER_NAME, _call_advisor, _extract_new_files  # noqa: E402

mcp = FastMCP(MCP_SERVER_NAME)


@mcp.tool()
async def db_query_ro(sql: str) -> str:
    """Run ONE read-only SQL SELECT against the production database
    (SELECT/WITH only). Returns rows as JSON."""
    ok, reason = guards.check_sql_readonly(sql)
    if not ok:
        raise ValueError(f'Rejected: {reason}')
    limit = settings.DEBUGGER_DB_ROW_LIMIT

    # The MCP server's stdio loop runs tool handlers on an asyncio event loop;
    # Django forbids sync ORM/DB-API access directly on that thread
    # (`SynchronousOnlyOperation`), so the actual query runs in a worker
    # thread — same pattern the old Agent SDK version used (asyncio.to_thread).
    def _run():
        # This MCP server is a long-lived stdio process, not a Django
        # request/response cycle, so the `request_started` signal that
        # normally closes stale connections never fires. Do the same check
        # it does — if Postgres (or a pooler) dropped the idle connection,
        # this closes the dead handle so the cursor below reconnects
        # instead of raising "connection already closed".
        connections['readonly'].close_if_unusable_or_obsolete()
        with connections['readonly'].cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchmany(limit + 1)
            return cols, rows

    try:
        cols, rows = await asyncio.to_thread(_run)
    except Exception as exc:
        raise ValueError(f'Query error: {exc}') from exc
    truncated = len(rows) > limit
    rows = rows[:limit]
    payload = {
        'columns': cols,
        'row_count': len(rows),
        'truncated': truncated,
        'rows': [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows],
    }
    return json.dumps(payload, default=str, indent=2)


@mcp.tool()
async def read_logs(source: str = 'app', grep: str = '', lines: int = 300) -> str:
    """Read server logs (read-only). `source` is one of "app" (journalctl -u
    the app unit), "nginx_access", "nginx_error". Optional `grep` filters
    lines; `lines` caps how many recent lines to return."""
    grep = (grep or '').strip()
    lines = max(1, min(int(lines or 300), 2000))
    try:
        # subprocess.run() is blocking; offload so it doesn't stall the
        # server's event loop while journalctl/tail runs.
        text = await asyncio.to_thread(_read_log_source, source, lines)
    except Exception as exc:
        raise ValueError(f'Log read error: {exc}') from exc
    if grep:
        kept = [ln for ln in text.splitlines() if grep.lower() in ln.lower()]
        text = '\n'.join(kept[-lines:]) or '(no matching lines)'
    return text or '(empty)'


@mcp.tool()
def propose_fix(diff: str, pr_title: str, pr_body: str, new_files: list = None) -> str:
    """Record a suggested fix as a unified diff plus PR title and body.
    Records the proposal only — it does NOT open a PR or write any files.
    IMPORTANT: put changes to EXISTING files in `diff` (as normal unified-diff
    hunks). Put brand-new files in `new_files` as full content instead of a
    diff hunk — hand-counting lines in a `@@ -0,0 +1,N @@` hunk for a large
    new file is exactly the kind of arithmetic mistake that corrupts the
    whole patch and breaks PR creation. `new_files` items look like
    {"path": "relative/path.py", "content": "full file contents"}."""
    validated = _extract_new_files(new_files)
    note = f' + {len(validated)} new file(s) attached as full content' if validated else ''
    return (f'Fix proposal recorded{note}. It will be shown to the admin '
            'with a "Create PR" button; no PR has been opened.')


@mcp.tool()
def record_learning(title: str, content: str) -> str:
    """Record ONE reusable lesson from this thread as a title + content. Used
    when finalizing a thread on close so future investigations benefit.
    Records only — the admin reviews it before it is saved."""
    return ('Learning drafted. The admin will review and edit it before it '
            'is saved to memory.')


@mcp.tool()
async def consult_advisor(summary: str) -> str:
    """Escalate to a stronger model for final synthesis — stating the root
    cause, drafting a fix diff, or writing a feature plan. Call this ONCE you
    have gathered enough evidence and are ready to produce the final answer,
    not while still exploring. The advisor has NO tool access — pass it
    everything it needs to judge (file:line excerpts, DB query results, log
    excerpts, your hypothesis) in the summary; it only sees what you write
    here."""
    summary = (summary or '').strip()
    if not summary:
        raise ValueError('Empty summary — nothing to advise on.')
    try:
        # _call_advisor blocks on a subprocess.run() call (a second `claude`
        # CLI invocation); offload so it doesn't stall the server's event loop.
        text = await asyncio.to_thread(_call_advisor, summary)
    except Exception as exc:
        raise ValueError(f'Advisor call failed: {exc}') from exc
    return text or '(advisor returned no text)'


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


if __name__ == '__main__':
    mcp.run(transport='stdio')
