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


# Directories that make an unscoped recursive scan (grep -r / find) run for a
# very long time (venv/site-packages, .git internals, bytecode caches). A scan
# that doesn't exclude these and isn't rooted at a specific subdirectory can
# burn most of the Celery soft time limit on its own.
_HEAVY_DIRS_RE = re.compile(r'venv|__pycache__|node_modules', re.IGNORECASE)
_ROOT_PATH_RE = re.compile(r'^(\.|\./|/|)$')


def _grep_scan_reason(tokens):
    """Return a deny reason for an unbounded recursive grep, or '' if fine."""
    flags = [t for t in tokens[1:] if t.startswith('-')]
    is_recursive = any(f in ('-r', '-R', '--recursive') for f in flags) or any(
        f.startswith('-') and not f.startswith('--') and 'r' in f[1:] for f in flags
    )
    if not is_recursive:
        return ''
    joined = ' '.join(tokens)
    if _HEAVY_DIRS_RE.search(joined) and '--exclude-dir' in joined:
        return ''
    # Recursive with no exclusion at all — could still be scoped to a small
    # subdirectory, but we can't tell "small" from "the whole repo root" here,
    # so require an explicit exclude for the known-heavy directories.
    return (
        "Recursive grep without --exclude-dir will also scan venv/ (~400MB) and "
        "can time out. Use 'rg' instead (it respects .gitignore), or add "
        "--exclude-dir=venv,__pycache__,.git and scope the path to a specific "
        "app directory (e.g. debugger/, catalog/)."
    )


def _find_scan_reason(tokens):
    """Return a deny reason for an unbounded find over the repo root, or ''."""
    path_arg = tokens[1] if len(tokens) > 1 and not tokens[1].startswith('-') else '.'
    if not _ROOT_PATH_RE.match(path_arg):
        return ''  # scoped to a real subdirectory — fine
    joined = ' '.join(tokens)
    if _HEAVY_DIRS_RE.search(joined) and '-prune' in joined:
        return ''
    return (
        "find over the repo root will also walk venv/ (~400MB) and can time "
        "out. Scope the path to a specific subdirectory (e.g. 'find debugger "
        "-name ...') or add a -prune clause excluding venv/__pycache__/.git."
    )


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
        tokens = tokens[idx:]
        exe = tokens[0].split('/')[-1]
        if exe not in ALLOWED_BASH_CMDS:
            return False, f'Command "{exe}" is not on the read-only allowlist.'
        if exe == 'git':
            sub = tokens[1] if len(tokens) > 1 else ''
            if sub == 'push':
                return False, 'git push is never allowed from the agent.'
            if sub and sub not in ALLOWED_GIT_SUBCMDS:
                return False, f'git {sub} is not a read-only subcommand.'
        if exe == 'grep':
            reason = _grep_scan_reason(tokens)
            if reason:
                return False, reason
        if exe == 'find':
            reason = _find_scan_reason(tokens)
            if reason:
                return False, reason
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
