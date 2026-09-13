"""
Entry point the `claude` CLI spawns (via --mcp-config) to talk to the
Debugger Agent's custom tools over stdio. See debugger/mcp_server.py for the
tool implementations and debugger/agent.py's _build_mcp_config for how this
command gets invoked (absolute path to manage.py + venv python, so it works
regardless of the CLI subprocess's cwd — see that function's docstring).

Not meant to be run by a human; `python manage.py run_debugger_mcp` blocks
serving stdio JSON-RPC until the parent (the `claude` CLI process) closes the
pipe.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Serve the Debugger Agent MCP tool server over stdio (spawned by the claude CLI).'

    def handle(self, *args, **opts):
        from debugger.mcp_server import mcp
        mcp.run(transport='stdio')
