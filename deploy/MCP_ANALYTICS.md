# Business Analysis MCP server — deployment

The `bizmcp` app exposes Varthaai's business data (sales, B2B pipeline,
stock, finance, marketing) as an MCP server, so Claude (claude.ai, Claude
Desktop, Claude Code) can be pointed at it directly for analysis — no admin
panel screen-sharing required.

**Hard guarantees**
- Read-only at every layer: curated tools (`bizmcp/tools.py`) only ever call
  ORM read methods; the raw-SQL fallback (`bizmcp/sql_tool.py`) runs against
  the same dedicated Postgres read-only role as the Debugger Agent
  (`readonly` DB alias, `default_transaction_read_only=on`) and is gated by
  `core/sql_guards.py` (single SELECT/CTE statement only).
- Reports at super_admin / all-brand scope by design (a personal analysis
  tool, not a multi-admin surface) — access is controlled entirely by the
  bearer token below, not by the admin panel's permission system.

## 1. Install runtime deps
```bash
source venv/bin/activate
pip install -r requirements.txt   # adds mcp, uvicorn
```

## 2. Reuse the existing read-only Postgres role
If the Debugger Agent is already deployed (`deploy/DEBUGGER.md`), the
`varthaai_ro` role and `DB_RO_*` env vars already exist — nothing more to
do. Otherwise, create it once as a DB superuser:
```sql
CREATE ROLE varthaai_ro LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE varthaai_db TO varthaai_ro;
GRANT USAGE ON SCHEMA public TO varthaai_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO varthaai_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO varthaai_ro;
```
Then set in `.env`:
```
DB_RO_USER=varthaai_ro
DB_RO_PASSWORD=choose-a-strong-password
```

## 3. Set the bearer token
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
Put the result in `.env`:
```
MCP_API_KEY=<generated token>
```
The server refuses every request with HTTP 500 if this is blank — it will
not silently run unauthenticated.

## 4. Start the service
```bash
sudo cp deploy/varthaai-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now varthaai-mcp
sudo journalctl -u varthaai-mcp -f
```
This runs `uvicorn bizmcp.asgi:application` on `127.0.0.1:8801` — a
separate process from the main app (`varthaai.service`, gunicorn on
`:8007`). Keep it to a single worker process; FastMCP's streamable-http
session state lives in that process's memory.

## 5. nginx
Already added to `deploy/varthaai.nginx.conf` (`upstream varthaai_mcp` +
`location /mcp`, unbuffered, long read timeout for the long-lived
streamable-http connection). Note the location has no trailing slash and
`proxy_pass http://varthaai_mcp;` has no trailing slash either — both
deliberate, so the exact path FastMCP forwards to (`/mcp`) reaches it
unrewritten. Reload after pulling the updated config:
```bash
sudo nginx -t && sudo systemctl reload nginx
```
The endpoint is `https://varthaai.com/mcp` (with or without a trailing
slash both work — nginx forwards either through unchanged, and FastMCP's
own router handles the redirect between them).

Sanity-check before wiring up a connector — with no/wrong token you should
get `401`, never `404` (a `404` here means the nginx path rewrite is wrong
again — see the note above) or Django's HTML 404 page (means nginx isn't
routing to the MCP upstream at all):
```bash
curl -i https://varthaai.com/mcp/                                    # expect 401
curl -i -H "Authorization: Bearer wrong" https://varthaai.com/mcp/   # expect 401
```

## 6. Add it as a connector in Claude
**claude.ai**: Settings → Connectors → Add custom connector
- URL: `https://varthaai.com/mcp`
- Header: `Authorization: Bearer <MCP_API_KEY>`

**Claude Code / Claude Desktop**: add a remote MCP server entry pointing at
the same URL and header. Once connected, ask things like:
- "What's our B2B outstanding and overdue right now?"
- "Which flavors need restocking?"
- "How did coupon SAVE10 perform last month?"

## 7. Verify write-safety
Confirm a mutating statement is rejected before it reaches Postgres (ask
Claude to try, or call the tool directly):
```
run_readonly_sql("UPDATE orders SET status='cancelled'")
# => {"error": "Rejected: Query contains a non-read-only keyword."}
```
And confirm the role itself can't write even if the guard were bypassed
(same check as `deploy/DEBUGGER.md` §4):
```bash
psql "host=127.0.0.1 dbname=varthaai_db user=varthaai_ro password=..." \
  -c "UPDATE orders SET status='cancelled'"
# => ERROR: cannot execute UPDATE in a read-only transaction
```

## Rotating the token
Change `MCP_API_KEY` in `.env` and restart the unit:
```bash
sudo systemctl restart varthaai-mcp
```
Update the connector's saved header in Claude to match.
