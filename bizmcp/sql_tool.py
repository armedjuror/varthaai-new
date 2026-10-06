"""
Raw read-only SQL fallback tool — for questions the curated tools in
tools.py don't cover. Mirrors the Debugger Agent's `db_query_ro`
(debugger/agent.py) but is registered as a real MCP tool for an external
Claude client rather than an in-process agent-SDK tool.

Runs exclusively against the `readonly` DB alias (a dedicated Postgres role
with `default_transaction_read_only=on` — see deploy/DEBUGGER.md §4, shared
with the Debugger Agent; no new role needed) and is gated by
`core.sql_guards.check_sql_readonly` (single SELECT/CTE statement only, no
DDL/DML keywords, no statement chaining).
"""
from django.conf import settings
from django.db import connections

from core.sql_guards import check_sql_readonly


def _jsonable(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def run_readonly_sql(sql):
    """Run ONE read-only SQL SELECT (or WITH ... SELECT) against the
    production database. Use describe_schema first to check table/column
    names. Returns rows as JSON, capped at MCP_DB_ROW_LIMIT rows."""
    ok, reason = check_sql_readonly(sql)
    if not ok:
        return {'error': f'Rejected: {reason}'}
    limit = settings.MCP_DB_ROW_LIMIT
    try:
        with connections['readonly'].cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = cur.fetchmany(limit + 1)
    except Exception as exc:
        return {'error': f'Query error: {exc}'}
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {
        'columns': cols,
        'row_count': len(rows),
        'truncated': truncated,
        'rows': [{c: _jsonable(v) for c, v in zip(cols, r)} for r in rows],
    }
