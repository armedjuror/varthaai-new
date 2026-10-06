"""
Bearer-token gate for the MCP HTTP endpoint. There is no Django session here
(this is a standalone ASGI process, not behind the admin panel's auth) — a
single static token in settings.MCP_API_KEY is the entire auth model,
appropriate for a personal analysis tool with super_admin/all-brand scope.
Rotate by changing the env var and restarting the unit.
"""
import hmac
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


class BearerTokenMiddleware:
    """Plain ASGI middleware: rejects any HTTP request whose
    `Authorization: Bearer <token>` header doesn't match MCP_API_KEY.
    Non-HTTP scopes (e.g. lifespan) pass through untouched."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        expected = settings.MCP_API_KEY
        if not expected:
            return await self._deny(send, 500, 'MCP_API_KEY is not configured on the server.')

        headers = dict(scope.get('headers') or [])
        raw = headers.get(b'authorization', b'').decode('latin-1')
        token = raw[7:] if raw.lower().startswith('bearer ') else ''
        if not token or not hmac.compare_digest(token, expected):
            # TEMP DIAGNOSTIC (remove once the 401 mismatch is root-caused):
            # never logs the header/token content, only shape, so it's safe
            # to leave in journalctl output.
            logger.warning(
                'bizmcp auth reject: header_present=%s raw_len=%d '
                'starts_with_bearer=%s token_len=%d expected_len=%d '
                'stripped_match=%s casefold_match=%s',
                b'authorization' in headers, len(raw),
                raw.lower().startswith('bearer '), len(token), len(expected),
                token.strip() == expected.strip(), token.casefold() == expected.casefold(),
            )
            return await self._deny(send, 401, 'Unauthorized.')

        return await self.app(scope, receive, send)

    @staticmethod
    async def _deny(send, status, message):
        body = message.encode('utf-8')
        await send({
            'type': 'http.response.start',
            'status': status,
            'headers': [(b'content-type', b'text/plain; charset=utf-8')],
        })
        await send({'type': 'http.response.body', 'body': body})


class StripMcpTrailingSlashMiddleware:
    """FastMCP mounts its app at exactly /mcp and issues its own 307 redirect
    for /mcp/ -> /mcp. Confirmed in production: at least one real MCP client
    drops the Authorization header when following that redirect (a common,
    reasonable security default in HTTP clients), silently turning a
    correctly-configured request into an unauthenticated one. Normalize the
    one case that matters (/mcp/ itself) before it reaches FastMCP's router,
    so the redirect — and the header loss that comes with it — never
    happens, regardless of which exact URL a client was configured with.
    Sub-paths like /mcp/foo are untouched; FastMCP's Mount handles those
    directly without a redirect."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and scope.get('path') == '/mcp/':
            scope = dict(scope)
            scope['path'] = '/mcp'
        return await self.app(scope, receive, send)
