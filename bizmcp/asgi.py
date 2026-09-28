"""
ASGI entrypoint for the business-analysis MCP server — a separate process
from the main Django app (Varthaai/asgi.py), served over streamable HTTP by
uvicorn: `uvicorn bizmcp.asgi:application --host 127.0.0.1 --port 8801`.

Run as a single process (see bizmcp/server.py's module docstring for why).
"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Varthaai.settings')
django.setup()

from bizmcp.server import build_asgi_app  # noqa: E402 — after django.setup()

application = build_asgi_app()
