# Debugger Agent — deployment

The Debugger Agent is a super-admin-only page (`/admin/debugger/`) that runs a
locked-down Claude Agent SDK investigation for each bug / feature / query, using
three read-only sources: the code, the database (SELECT-only), and server logs.

**Hard guarantees**
- The agent never writes to the application DB (a dedicated Postgres read-only
  role + `default_transaction_read_only=on` + a SELECT-only check in the tool).
- The agent never writes files or pushes git (tool allowlist + a `PreToolUse`
  deny-hook; see `debugger/guards.py`, unit-tested in `debugger/tests.py`).
- Phase 1 is analysis only — no PR is created. (PR automation is Phase 2.)

## 1. Rotate the Anthropic key
A live-looking `ANTHROPIC_API_KEY` was previously present in `.env`. **Rotate it**
in the Anthropic console, then set the new value in `.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
DEBUGGER_MODEL=claude-sonnet-5
```

## 2. Install runtime deps
```bash
source venv/bin/activate
pip install -r requirements.txt          # adds celery, redis, claude-agent-sdk
```
The Claude Agent SDK spawns the `claude` CLI (Node.js). Install it on the box:
```bash
# Node 18+ required
npm install -g @anthropic-ai/claude-code   # provides the `claude` binary
which claude                                # must resolve on the worker's PATH
```

## 3. Redis (Celery broker)
```bash
sudo apt install redis-server
sudo systemctl enable --now redis-server
```
Defaults to `redis://127.0.0.1:6379/0` (override via `CELERY_BROKER_URL`).

## 4. Dedicated read-only Postgres role
Run once as a DB superuser (e.g. `sudo -u postgres psql -d varthaai_db`):
```sql
CREATE ROLE varthaai_ro LOGIN PASSWORD 'choose-a-strong-password';
GRANT CONNECT ON DATABASE varthaai_db TO varthaai_ro;
GRANT USAGE ON SCHEMA public TO varthaai_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO varthaai_ro;
-- future tables too:
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO varthaai_ro;
```
Then set in `.env`:
```
DB_RO_USER=varthaai_ro
DB_RO_PASSWORD=choose-a-strong-password
# DB_RO_NAME / DB_RO_HOST / DB_RO_PORT default to the main DB values
```
Verify it truly cannot write:
```bash
psql "host=127.0.0.1 dbname=varthaai_db user=varthaai_ro password=..." \
  -c "INSERT INTO debug_requests(kind,title) VALUES ('bug','x');"
# => ERROR: permission denied  (and/or) cannot execute INSERT in a read-only transaction
```

## 5. Migrate + collect static
```bash
python manage.py migrate
python manage.py collectstatic --noinput
```

## 6. Log access
The `read_logs` tool runs `journalctl -u varthaai` and tails the nginx logs. The
worker user (`ubuntu`) must be able to read them:
```bash
sudo usermod -aG systemd-journal,adm ubuntu   # re-login / restart the worker after
```
Override paths via `DEBUGGER_NGINX_ACCESS_LOG` / `DEBUGGER_NGINX_ERROR_LOG` and
the unit name via `DEBUGGER_LOG_UNIT` in `.env`.

## 7. Start the worker
```bash
sudo cp deploy/varthaai-celery.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now varthaai-celery
sudo journalctl -u varthaai-celery -f
```
Restart the web app too (`sudo systemctl restart varthaai`) so the new settings/
routes load.

## 8. Provision swap (prevents OOM kills)
Each investigation spawns a `claude` CLI (Node) that can hold **0.5–1 GB+ RSS**
on a long agentic run — on top of gunicorn, Celery, Postgres and Redis on the same
box. On a small instance (1–2 GB) this OOM-kills the worker mid-run:
`kernel OOM killer killed some processes` / the CLI dies with `exit code -9`. Add
swap so a memory spike pages out instead of killing the process:
```bash
sudo fallocate -l 4G /swapfile        # 4 GB is comfortable; 2 GB is the minimum
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # persist across reboot
free -h                                # confirm Swap: shows the new space
```
The worker unit is already set to `--concurrency=1` (one investigation at a time)
and `--max-tasks-per-child=1` (recycle the child after each run to release the
CLI's memory). If OOMs persist even with swap, the box is undersized — move to an
instance with ≥2 GB RAM (t3.small or larger).

## Phase 2 — PR automation + review loop
On admin approval the agent opens a PR, and (via the beat poller) reads back PR
reviews to revise the branch. All git/GitHub work runs in an **isolated clone**
at `DEBUGGER_REPO_DIR` — never the prod checkout, never a push to `main`, never a
force-push.

1. **`gh` CLI + token.** Install GitHub CLI and set a token with `repo` scope
   (a fine-grained PAT or `gh auth token`). Keep it out of git.
   ```bash
   sudo apt install -y gh          # or the official gh apt repo
   gh --version
   ```
   In `.env`:
   ```
   GITHUB_TOKEN=ghp_...            # repo scope; used by git push + gh
   GITHUB_REPO=armedjuror/varthaai-new
   GITHUB_DEFAULT_BRANCH=main
   DEBUGGER_REPO_DIR=/home/ubuntu/varthaai-debugger-worktree
   DEBUGGER_PR_POLL_SECONDS=300
   ```
   The isolated clone is created automatically on first PR (`git clone` via an
   `x-access-token` remote); pre-create it if you prefer.

2. **Beat.** The worker unit runs `celery ... worker -B`, which includes the beat
   scheduler that polls open PRs every `DEBUGGER_PR_POLL_SECONDS`. Reviewers'
   comments are ingested into the thread and the agent revises the same branch or
   asks you a question. You can also hit the **⟳** button on a PR thread to sync
   on demand.

3. **Safety checks (already enforced, worth verifying):**
   - `debugger/pr.py:_assert_pushable()` refuses to push `main`.
   - Revisions are plain commits (no `--force`).
   - Restart the worker after adding the token: `sudo systemctl restart varthaai-celery`.

**Flow recap:** bug/feature reaches `ready` with a proposed diff → admin clicks
**Create PR** → `debugger/<kind>-<id>-<slug>` branch pushed + PR opened → reviews
ingested → agent pushes revisions or replies → repeat until you merge.
