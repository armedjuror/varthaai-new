"""
Generic runner for the `claude` CLI in headless mode (`claude -p ...`), shared
by every agentic-Claude app in this codebase (`debugger`, `content`). Nothing
here is app-specific — callers supply their own prompt, tool allow/deny lists,
MCP server config, and hook settings; this module only knows how to (a)
enforce a global concurrency cap on `claude` subprocesses, (b) shell out to
one authenticated off the CLI's own login session (never ANTHROPIC_API_KEY —
see run_claude_cli's docstring), and (c) parse its output. This applies even
to calls that need no tools at all (pure text/JSON generation, e.g. the
Content Studio's Planner Agent or the Debugger Agent's advisor) — those still
go through this function with an empty tool allowlist, not a direct
`anthropic` SDK call, so there is exactly one way this codebase talks to
Claude and exactly one place auth is decided.

Why a Redis-backed semaphore instead of an in-process one: deployment runs a
single `celery -A Varthaai worker -B` process, but Celery can still fan work
out across multiple prefork child processes (CELERY_WORKER_MAX_TASKS_PER_CHILD
is set; --concurrency is not pinned to 1 in general), and a future `content`
app's tasks must coordinate against the SAME limit. An in-memory
threading.Semaphore inside one task module would only protect that one
process — a second child process (or the content app's tasks) would freely
blow past the cap. Redis is already a hard dependency (Celery's broker), so
the semaphore lives there instead of adding new infra.

Crash safety: each held slot is a lease with a TTL, renewed (heartbeated) by
a background thread for as long as the holding process is alive. If that
process dies (OOM-killed, `kill -9`, host reboot) mid-call, the heartbeat
thread dies with it and the lease simply expires within CLAUDE_CLI_LEASE_TTL
_SECONDS — capacity self-heals with no separate recovery step (unlike
debugger/apps.py's requeue_orphaned_requests, which exists to recover DB rows
that have no TTL of their own). This also means the TTL bounds "how long a
slot can stay stuck after a crash", not "how long a single call may run" —
long calls stay alive via the heartbeat regardless of the TTL value.
"""
import contextlib
import json
import logging
import os
import subprocess
import threading
import time
import uuid

import redis
from django.conf import settings

logger = logging.getLogger(__name__)


class ClaudeCliError(RuntimeError):
    """The `claude` CLI subprocess failed (nonzero exit, timeout, or produced
    no parseable result)."""


class ClaudeCliBusyError(RuntimeError):
    """No concurrency slot became available within acquire_timeout."""


# Every built-in tool name the CLI recognizes — pass as `disallowed_tools` for
# a call that needs zero tool access (pure text/JSON in, text/JSON out): plain
# structured generation, captioning, "write me a plan as JSON", etc. Belt-and-
# braces alongside not passing `allowed_tools` at all. Shared so every such
# caller (content.agents.planner, debugger.agent._call_advisor,
# debugger/management/commands/compress_learnings.py, ...) denies the exact
# same list rather than each keeping a slightly different copy.
NO_TOOLS = ['Read', 'Grep', 'Glob', 'Bash', 'Write', 'Edit', 'NotebookEdit',
            'WebFetch', 'WebSearch', 'TodoWrite']


# --------------------------------------------------------------------------- #
# Redis-backed counting semaphore                                             #
# --------------------------------------------------------------------------- #
# Atomically prune expired leases, then admit the new one IFF the (post-prune)
# count is still under the limit. One round-trip, one script — no separate
# "count then add" race between concurrent acquirers on different hosts.
_ACQUIRE_LUA = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
local count = redis.call('ZCARD', KEYS[1])
if count < tonumber(ARGV[4]) then
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
    return 1
end
return 0
"""

# Only renew if the lease is still ours (hasn't already expired and been
# pruned by someone else's acquire) — otherwise silently report failure so
# the caller can log it rather than resurrecting a lease that's already been
# handed to another waiter.
_RENEW_LUA = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
    redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
    return 1
end
return 0
"""

_RELEASE_LUA = "return redis.call('ZREM', KEYS[1], ARGV[1])"


class RedisSlotSemaphore:
    """A Redis ZSET-backed counting semaphore. See module docstring for the
    crash-safety design (TTL leases + heartbeat renewal, no in-memory state)."""

    def __init__(self, redis_client, key, limit, lease_ttl):
        self._redis = redis_client
        self.key = key
        self.limit = limit
        self.lease_ttl = lease_ttl
        self._renew_interval = max(5, lease_ttl // 3)
        self._acquire_script = redis_client.register_script(_ACQUIRE_LUA)
        self._renew_script = redis_client.register_script(_RENEW_LUA)
        self._release_script = redis_client.register_script(_RELEASE_LUA)

    @contextlib.contextmanager
    def acquire(self, acquire_timeout, poll_interval=0.25, poll_interval_max=2.0):
        """Block (with capped-backoff polling, not a tight loop) until a slot
        is free or `acquire_timeout` elapses, then hold it — heartbeating a
        renewal in the background — until the `with` block exits."""
        token = uuid.uuid4().hex
        deadline = time.monotonic() + acquire_timeout
        interval = poll_interval
        waited_log_at = time.monotonic()
        while True:
            now = time.time()
            got = self._acquire_script(
                keys=[self.key], args=[now, now + self.lease_ttl, token, self.limit])
            if got:
                break
            if time.monotonic() >= deadline:
                raise ClaudeCliBusyError(
                    f'No claude CLI concurrency slot available after waiting '
                    f'{acquire_timeout}s (limit={self.limit}).')
            if time.monotonic() - waited_log_at > 30:
                logger.info('claude_cli: still waiting for a concurrency slot '
                            '(limit=%s)', self.limit)
                waited_log_at = time.monotonic()
            time.sleep(interval)
            interval = min(interval * 1.5, poll_interval_max)

        stop_event = threading.Event()

        def _renew_loop():
            while not stop_event.wait(self._renew_interval):
                try:
                    ok = self._renew_script(
                        keys=[self.key], args=[token, time.time() + self.lease_ttl])
                    if not ok:
                        logger.warning(
                            'claude_cli: lease %s expired before it could be '
                            'renewed (heartbeat interval too close to TTL, or '
                            'the process stalled) — another waiter may now '
                            'hold this slot concurrently.', token)
                except Exception:
                    logger.exception('claude_cli: lease renewal failed')

        renewer = threading.Thread(target=_renew_loop, daemon=True)
        renewer.start()
        try:
            yield
        finally:
            stop_event.set()
            renewer.join(timeout=5)
            try:
                self._release_script(keys=[self.key], args=[token])
            except Exception:
                # Not fatal — the TTL will expire the lease on its own.
                logger.exception('claude_cli: slot release failed (lease will '
                                 'still expire via TTL)')


_semaphore = None
_semaphore_lock = threading.Lock()
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(settings.CLAUDE_CLI_REDIS_URL)
    return _redis_client


def get_semaphore():
    global _semaphore
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                _semaphore = RedisSlotSemaphore(
                    _get_redis(),
                    settings.CLAUDE_CLI_SEMAPHORE_KEY,
                    settings.CLAUDE_CLI_MAX_CONCURRENT,
                    settings.CLAUDE_CLI_LEASE_TTL_SECONDS,
                )
    return _semaphore


# --------------------------------------------------------------------------- #
# CLI runner                                                                   #
# --------------------------------------------------------------------------- #
def run_claude_cli(
    prompt,
    *,
    system_prompt=None,
    allowed_tools=None,
    disallowed_tools=None,
    mcp_config=None,
    strict_mcp_config=True,
    extra_settings=None,
    model=None,
    cwd=None,
    env=None,
    max_turns=None,
    timeout=None,
    acquire_timeout=None,
    dangerously_skip_permissions=True,
):
    """
    Run one headless `claude -p` turn under the global concurrency limit and
    return a parsed dict:
      {
        'text': str,                          # final assistant text
        'tool_calls': [{'name': str, 'input': dict}, ...],  # call order
        'usage': dict,                        # token/cost usage
        'num_turns': int | None,
        'is_error': bool,
        'subtype': str | None,
        'session_id': str | None,
        'total_cost_usd': float | None,
        'duration_ms': int | None,
      }

    Parameters map directly onto CLI flags:
      system_prompt        -> --system-prompt (full override, not appended —
                               matches the old Agent SDK's `system_prompt=`)
      allowed_tools /
      disallowed_tools     -> --allowedTools / --disallowedTools (lists)
      mcp_config            -> --mcp-config (dict, serialized to a JSON string,
                               or an already-built path/JSON string)
      strict_mcp_config     -> --strict-mcp-config (only used if mcp_config given)
      extra_settings        -> merged into --settings <json> (e.g. {'hooks': ...})
      max_turns              -> merged into extra_settings as {'maxTurns': N}
      model                  -> --model
      cwd / env               -> subprocess cwd / extra env vars (merged over
                                 os.environ, NOT a replacement)
      timeout                -> subprocess wall-clock timeout (seconds)
      acquire_timeout        -> how long to wait for a concurrency slot before
                                 raising ClaudeCliBusyError (defaults to
                                 settings.CLAUDE_CLI_ACQUIRE_TIMEOUT_SECONDS)
      dangerously_skip_permissions -> --dangerously-skip-permissions (default
                                 True; callers that need Claude's own
                                 interactive-style approval gate can disable
                                 this, but headless/Celery callers always want
                                 it — approval gating for this agent is done
                                 via a PreToolUse hook in `extra_settings`,
                                 not Claude's permission prompt, which has no
                                 TTY to prompt on anyway)

    Always runs with --setting-sources '' (hermetic: no ~/.claude, no project
    .claude/settings.json, no auto-discovered CLAUDE.md from `cwd` — verified
    empirically, since `cwd` is often a real checkout of this repo, which has
    its own .claude/settings.json). Pass whatever context you need explicitly
    via `system_prompt`.

    No service in this codebase authenticates with ANTHROPIC_API_KEY — every
    `claude` call goes through this function and runs off the CLI's own
    logged-in session (`claude login`; verified empirically: `claude -p ...`
    with ANTHROPIC_API_KEY unset in the environment still resolves and reports
    `"apiKeySource":"none"`). ANTHROPIC_API_KEY is stripped from the
    subprocess environment unconditionally, even if present in the parent
    process's os.environ (e.g. a stray .env value) or passed in via `env=` —
    if it's present, the CLI prefers API-key billing over the login session,
    silently defeating this guarantee, so there is no opt-out.

    Raises ClaudeCliBusyError / ClaudeCliError as described above.
    """
    settings_obj = dict(extra_settings or {})
    if max_turns:
        settings_obj['maxTurns'] = max_turns

    argv = [
        settings.CLAUDE_CLI_BIN, '-p', prompt,
        '--output-format', 'stream-json', '--verbose',
        '--setting-sources', '',
    ]
    if system_prompt is not None:
        argv += ['--system-prompt', system_prompt]
    if model:
        argv += ['--model', model]
    if allowed_tools:
        argv += ['--allowedTools', ','.join(allowed_tools)]
    if disallowed_tools:
        argv += ['--disallowedTools', ','.join(disallowed_tools)]
    if mcp_config:
        mcp_arg = json.dumps(mcp_config) if isinstance(mcp_config, dict) else str(mcp_config)
        argv += ['--mcp-config', mcp_arg]
        if strict_mcp_config:
            argv.append('--strict-mcp-config')
    if settings_obj:
        argv += ['--settings', json.dumps(settings_obj)]
    if dangerously_skip_permissions:
        argv.append('--dangerously-skip-permissions')

    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    # Unconditional, after the env= merge — see the docstring above for why
    # this has no opt-out.
    run_env.pop('ANTHROPIC_API_KEY', None)

    resolved_acquire_timeout = (
        acquire_timeout if acquire_timeout is not None
        else settings.CLAUDE_CLI_ACQUIRE_TIMEOUT_SECONDS)
    semaphore = get_semaphore()

    wait_start = time.monotonic()
    with semaphore.acquire(acquire_timeout=resolved_acquire_timeout):
        waited = time.monotonic() - wait_start
        if waited > 1:
            logger.info('claude_cli: waited %.1fs for a concurrency slot', waited)
        try:
            proc = subprocess.run(
                argv, cwd=cwd, env=run_env, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(f'claude CLI timed out after {timeout}s') from exc

    if proc.stderr:
        logger.debug('claude_cli stderr: %s', proc.stderr[-4000:])

    if proc.returncode != 0:
        raise ClaudeCliError(
            f'claude CLI exited {proc.returncode}: '
            f'{(proc.stderr or proc.stdout or "").strip()[-2000:]}')

    return _parse_stream_json(proc.stdout)


def _parse_stream_json(stdout):
    """Parse --output-format stream-json (newline-delimited JSON) output into
    the dict shape documented on run_claude_cli. Tool calls are recovered
    straight from the assistant `tool_use` content blocks in the stream — this
    works across the process boundary (unlike the old Agent SDK, which could
    have MCP tool handlers append directly to a Python list in the parent
    process; a `claude` CLI subprocess and its MCP-server children cannot)."""
    tool_calls = []
    result_event = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        obj_type = obj.get('type')
        if obj_type == 'assistant':
            for block in (obj.get('message') or {}).get('content') or []:
                if block.get('type') == 'tool_use':
                    tool_calls.append({
                        'name': block.get('name'),
                        'input': block.get('input') or {},
                    })
        elif obj_type == 'result':
            result_event = obj

    if result_event is None:
        raise ClaudeCliError(
            'claude CLI produced no result event (unparseable stream-json output).')

    return {
        'text': result_event.get('result') or '',
        'tool_calls': tool_calls,
        'usage': result_event.get('usage') or {},
        'num_turns': result_event.get('num_turns'),
        'is_error': bool(result_event.get('is_error')),
        'subtype': result_event.get('subtype'),
        'session_id': result_event.get('session_id'),
        'total_cost_usd': result_event.get('total_cost_usd'),
        'duration_ms': result_event.get('duration_ms'),
    }
