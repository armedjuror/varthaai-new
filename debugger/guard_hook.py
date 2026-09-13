#!/usr/bin/env python
"""
PreToolUse guard hook for the Debugger Agent, run by the `claude` CLI itself
(registered via `--settings` -> hooks.PreToolUse -> {"type": "command", ...}).

This replaces the Claude Agent SDK's in-process `HookMatcher` callback (which
required a running Python event loop inside the same process as the caller —
not possible once the agent runs as a separate `claude` CLI subprocess). The
CLI hook protocol is: it receives one JSON object on stdin (with `tool_name` /
`tool_input` among other fields) per tool-call-about-to-happen, and expects a
JSON object on stdout describing the decision — same shape either mechanism
uses, so the actual policy logic (debugger/guards.py) is unchanged and still
covered by debugger/tests.py.

Deliberately Django-free (no `django.setup()`): this process is spawned fresh
for every single tool call the agent makes, so keeping it lightweight matters.
debugger.guards only imports stdlib (re, shlex) — see its docstring for the
exact guarantees enforced here (no Write/Edit, no non-read-only Bash, no git
push — verified in debugger/tests.py's BashGuardTests/ToolGuardTests).
"""
import json
import sys
from pathlib import Path

# debugger/ is a plain package with no Django import at module load time, so
# this works without a Django settings module — just needs BASE_DIR on the
# path (this file lives at <BASE_DIR>/debugger/guard_hook.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from debugger import guards  # noqa: E402


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}
    tool_name = payload.get('tool_name', '') or ''
    tool_input = payload.get('tool_input') or {}
    decision, reason = guards.evaluate_tool(tool_name, tool_input)
    if decision == 'deny':
        output = {
            'hookSpecificOutput': {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'deny',
                'permissionDecisionReason': reason,
            }
        }
    else:
        output = {}
    sys.stdout.write(json.dumps(output))
    sys.exit(0)


if __name__ == '__main__':
    main()
