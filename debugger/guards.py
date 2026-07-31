"""
Pure guardrail logic for the Debugger Agent — kept dependency-free so it can be
unit-tested without the Claude SDK or a running CLI.

Two guarantees enforced here (in addition to the DB-level read-only role):
  * The agent may never write to disk or mutate git state.
  * Any SQL run through db_query_ro is a single read-only SELECT.
"""
import re

# Tools the agent is never allowed to invoke (writes / side effects).
BLOCKED_TOOLS = {
    'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'NotebookWrite',
}

# Bash executables the agent MAY run — everything read-only. Anything not in
# this set (or any git subcommand that mutates) is denied. Deliberately excludes
# interpreters (python/awk/sed) and network tools — they can write/execute.
ALLOWED_BASH_CMDS = {
    'grep', 'rg', 'cat', 'ls', 'find', 'head', 'tail', 'wc', 'sort', 'uniq',
    'cut', 'tr', 'echo', 'pwd', 'stat', 'file', 'tree', 'which',
    'journalctl', 'git', 'true',
}

# Substrings that make an otherwise-allowlisted command dangerous (write/execute
# primitives, command substitution). Checked case-insensitively.
DANGEROUS_SUBSTRINGS = (
    '-delete', '-exec', '-execdir', '-fprint', '-ok',   # find write/exec primitives
    '$(', '`', 'xargs',                                  # command substitution / chaining
)

# git subcommands that are read-only. `push`, `commit`, `checkout -b`, etc. are
# blocked here; branch/commit/push for a real PR happens only in the isolated
# worktree path (Phase 2), never via the agent's Bash tool.
ALLOWED_GIT_SUBCMDS = {
    'log', 'diff', 'show', 'status', 'blame', 'branch', 'rev-parse',
    'ls-files', 'ls-tree', 'cat-file', 'shortlog', 'describe', 'grep',
}

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


def _split_bash(command):
    """Best-effort tokenization; falls back to whitespace split on shlex error."""
    import shlex
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def check_bash_command(command):
    """Return (ok, reason) for a Bash tool invocation."""
    if not command or not command.strip():
        return False, 'Empty command.'

    # Reject writes/redirection/backgrounding outright.
    if re.search(r'(^|\s)(>|>>|\|\s*tee\b)', command):
        return False, 'Output redirection is not allowed (read-only agent).'

    lowered = command.lower()
    for bad in DANGEROUS_SUBSTRINGS:
        if bad in lowered:
            return False, f'Command contains a disallowed construct: "{bad}".'

    # Evaluate every segment of a compound command (;, &&, ||, |).
    segments = re.split(r'(?:&&|\|\||\||;)', command)
    for seg in segments:
        tokens = _split_bash(seg)
        if not tokens:
            continue
        # Skip leading env-var assignments (FOO=bar cmd ...).
        idx = 0
        while idx < len(tokens) and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', tokens[idx]):
            idx += 1
        if idx >= len(tokens):
            continue
        exe = tokens[idx].split('/')[-1]
        if exe not in ALLOWED_BASH_CMDS:
            return False, f'Command "{exe}" is not on the read-only allowlist.'
        if exe == 'git':
            sub = tokens[idx + 1] if idx + 1 < len(tokens) else ''
            if sub == 'push':
                return False, 'git push is never allowed from the agent.'
            if sub and sub not in ALLOWED_GIT_SUBCMDS:
                return False, f'git {sub} is not a read-only subcommand.'
    return True, ''


def evaluate_tool(tool_name, tool_input):
    """
    Central gate used by the PreToolUse hook.
    Returns (decision, reason) where decision is 'allow' or 'deny'.
    """
    if tool_name in BLOCKED_TOOLS:
        return 'deny', f'{tool_name} is disabled — the debugger agent is read-only.'
    if tool_name == 'Bash':
        ok, reason = check_bash_command((tool_input or {}).get('command', ''))
        return ('allow', '') if ok else ('deny', reason)
    # Our own MCP tools self-validate; read-only builtins (Read/Grep/Glob) pass.
    return 'allow', ''
