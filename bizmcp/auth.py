"""
Bearer-token gate for the MCP HTTP endpoint. There is no Django session here
(this is a standalone ASGI process, not behind the admin panel's auth) — a
single static token in settings.MCP_API_KEY is the entire auth model,
appropriate for a personal analysis tool with super_admin/all-brand scope.
Rotate by changing the env var and restarting the unit.
"""
import hmac

from django.conf import settings


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
