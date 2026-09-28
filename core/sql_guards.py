"""
Shared SQL read-only guard — the single source of truth for "is this SQL a
safe single read-only statement". Used by any tool that runs ad hoc SQL
against the `readonly` DB alias (the Debugger Agent's `db_query_ro`,
`bizmcp`'s `run_readonly_sql`), so the rule is enforced identically and
tested once.
"""
import re

# Whole-word SQL keywords that indicate a write / DDL / side effect.
_FORBIDDEN_SQL = re.compile(
    r'\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|'
    r'copy|call|do|merge|comment|reindex|vacuum|lock|set|begin|commit|'
    r'rollback|savepoint|prepare|execute|listen|notify|refresh)\b',
    re.IGNORECASE,
)


def check_sql_readonly(sql):
    """Return (ok, reason). Enforces a single read-only SELECT/CTE statement."""
    if not sql or not sql.strip():
        return False, 'Empty query.'
    cleaned = sql.strip().rstrip(';').strip()
    # No statement chaining.
    if ';' in cleaned:
        return False, 'Multiple statements are not allowed — run one SELECT.'
    first = cleaned.split(None, 1)[0].lower()
    if first not in ('select', 'with'):
        return False, 'Only SELECT (or WITH ... SELECT) queries are allowed.'
    if _FORBIDDEN_SQL.search(cleaned):
        return False, 'Query contains a non-read-only keyword.'
    return True, ''
