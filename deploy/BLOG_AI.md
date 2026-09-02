# Blog AI Writing Assistant — deployment notes

Adds a litellm-backed writing assistant to the blog editor
(`/admin/blogs/new/`, `/admin/blogs/<id>/edit/`), streamed to the browser via
Server-Sent Events (SSE) from `marketing.views.BlogAIStreamView` /
`marketing.ai.stream_completion`. This is a SEPARATE, lightweight integration
from the `debugger` app's `claude-agent-sdk` (see `deploy/DEBUGGER.md`) — no
agentic tool-use, just plain per-turn chat completions, so the
provider/model is swappable per request via `settings.AI_ASSIST_MODELS`.

## 1. Install the new dependency
```bash
source venv/bin/activate
pip install -r requirements.txt   # adds litellm
```
`litellm` reuses the existing `ANTHROPIC_API_KEY` (`Varthaai/settings.py`) —
no new secret needed. **Verify the model id** in `settings.AI_ASSIST_MODELS`
(`anthropic/claude-sonnet-5`) against Anthropic's current model list before
relying on it in production — it was carried over from `DEBUGGER_MODEL`'s
existing default without independent verification (no live API access was
available while drafting this feature). Also double-check the pinned
`litellm` version in `requirements.txt` — same caveat, no PyPI access here.

## 2. Migrate
```bash
python manage.py migrate marketing
```

## 3. Streaming infrastructure — real gaps, not cosmetic

This is the **first SSE endpoint in the app** (`/admin/api/blogs/ai/stream/`).
The existing `debugger` chat UI deliberately avoids this by polling
(`static/js/admin/debugger.js::schedulePoll`) — this feature is the first
thing that actually needs it. Current infra is not set up for it:

- **`deploy/gunicorn.conf.py`**: `worker_class = "sync"`, `timeout = 60`.
  A sync worker holds the connection (and one whole worker slot) for the
  full length of a generation. **Gunicorn's arbiter timeout is wall-clock
  from request start and is NOT reset by bytes sent on a streaming
  response** — a full draft generation that runs past 60s gets the worker
  **SIGKILLed mid-stream**, not just slow. The client
  (`static/js/admin/blog-editor.js`) already treats a dropped connection as
  "keep whatever streamed so far as editable text, don't hang" — but the
  request still fails server-side and nothing in application code can catch
  a SIGKILL.
  - Interim mitigation: raise `timeout` in `deploy/gunicorn.conf.py`. Do this
    deliberately, not blindly — a global timeout bump also masks unrelated
    slow-request bugs elsewhere in the app.
  - Real fix (out of scope for this PR): move to an async-capable worker
    class (`gevent`/`eventlet`) for this app, or split the AI endpoints onto
    their own gunicorn process suited to long-lived streaming connections.
- **Concurrency ceiling.** `workers = min(cpu_count()*2+1, 5)` — at most 5
  sync workers total, shared with every other admin request. Each concurrent
  AI generation pins one worker for up to the full timeout window; a
  handful of admins generating drafts at once can stall the rest of the
  admin panel. Worth monitoring in practice, not just in theory.
- **`deploy/varthaai.nginx.conf`**: the single `location /` block sets
  `proxy_read_timeout 60s` with no `proxy_buffering off`. Add a dedicated
  location block ahead of `location /` for the stream path so nginx doesn't
  buffer the whole SSE response before forwarding it:
  ```nginx
  location /admin/api/blogs/ai/stream/ {
      proxy_pass http://varthaai_app;
      proxy_http_version 1.1;
      proxy_set_header Host              $host;
      proxy_set_header X-Real-IP         $remote_addr;
      proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_buffering off;
      proxy_read_timeout 300s;
  }
  ```
  The Django view already sets `X-Accel-Buffering: no` on the response as a
  belt-and-suspenders measure, but that header has no effect unless nginx's
  `proxy_buffering` is also addressed for this path — do both.
- **Checked, not a blocker**: `MIDDLEWARE` in `Varthaai/settings.py` has no
  `GZipMiddleware` (which would buffer the entire response and break SSE
  outright) — confirmed absent, no code change needed there. Re-check this
  if compression middleware is ever added later.

## 4. Cost/usage auditing
Each assistant turn is persisted on `marketing.BlogDraftMessage`
(`provider`, `model`, `prompt_tokens`, `completion_tokens`, `cost_usd` via
`litellm.completion_cost`) — query that table directly for spend audits; no
dashboard UI is built yet.
