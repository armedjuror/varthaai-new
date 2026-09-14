# Varthaai Content Generator — System Plan (POC)

A Django-native pipeline that turns "what should we post this month" into
approved scripts, Q&A sheets, and posters, using three agents (Planner,
Copywriter, Designer) built the same way as the existing `debugger` app:
the `claude` CLI (headless, via a shared concurrency-limited utility),
Celery beat for scheduling, Django models for state, admin-panel pages
for review.

This plan assumes and builds on `poster-generation-plan.md` (the Designer
Agent's image pipeline) and the five Claude Skills already authored and
staged at `content/skills/` (see §1).

---

## 0. Corrections to CLAUDE.md's placeholder description

CLAUDE.md describes Varthaai generically ("food brand (peanut butter etc.)").
The actual brand facts, taken from the skill files, override that for
anything content-related:

- **Product**: premium Kerala banana chips.
- **Founders**: Ajwad (co founder, CEO, does on-camera things for Varthaanm/Verdict) and Saad
  (co-founder, COO).
- **Audience for Learn With Varthaai**: Bengaluru — hence Kannada +
  Malayalam + English.

---

## 1. The four series + one support skill (already authored)

All five already exist as production-grade Claude Skills, staged at
`content/skills/<name>/SKILL.md` (+ `references/` for `food-quality-analyst`).
**These are not reimplemented in Python.** The Copywriter Agent runs them
as-is inside a `claude` CLI session — rewriting this judgment into code
would only degrade it (FSSAI citation rules, Kannada verification steps,
controversial-finding handling are all deliberately encoded as instructions,
not data).

| Series | Format | Cadence | Prep timing | Skill | Needs from Ajwad before it can run |
|---|---|---|---|---|---|
| **Varthaai Varthaanm** | Reel — narrated VO script + b-roll notes | Every Friday | Sunday before | `varthaai-varthaanm-script` | This week's story/incident; last episode's recap (see §6 memory note); cliffhanger-or-not call |
| **Varthaai Verdict** | Reel — on-camera talking points (not narrated), Ajwad ad-libs | Every Wednesday, starting **23 Sept 2026** | ≥3 days before / Sunday before that week | `varthaai-verdict-script` (calls `food-quality-analyst` internally) | A physically bought, tasted, photographed competitor product; ingredients + nutrition label (photo/text); Ajwad's Design/Pricing/Taste notes + scores /10 |
| **Learn With Varthaai** | **Poster** (v1), reel later | Flexible — Planner slots it into the month | 2 days before publish (poster gen timing) | `learn-with-varthaai-script` | A situation or phrase, or a theme to pick from |
| **Varthaai Inside** | Reel — interview Q&A prep sheet, not scripted | **Every alternate Monday** | **8 days before** (the Sunday before the Sunday before — "previous to previous Sunday") | `varthaai-inside-questions` | Episode topic |
| *(support)* `food-quality-analyst` | — | — | — | Ingredient/nutrition research, called only from Verdict | Ingredients + nutrition label + category + market |

**Build-order note**: Varthaai Verdict is deliberately **the last series
automated** (§13, Phase 5) — Varthaanm, Learn With Varthaai, Varthaai
Inside, occasion posters, and Blog come first. Since Verdict launches
Wed 23 Sep, well before its own Phase 5 lands, its first several episodes
will be scripted the way you do it today — running `varthaai-verdict-script`
and `food-quality-analyst` yourself in a chat — until the pipeline catches
up. The Planner still reserves Verdict's Wednesday slots on the calendar
from day one; only the automated script-generation step is deferred.

**Plus, newly in scope:** Blog posts (`marketing.Blog`) and ad-hoc
posters (occasions/festivals/trends outside the four fixed series).
Simplified per your latest framing — the four reel series above stay
fully skill-bound, no change (*"if it's a script for a reel, it should be
according to the skill"*), but Blog and ad-hoc posters don't each need
their own elaborate skill document. They're the same underlying
Copywriter engine, just given the right **context** for what it's
producing: a `content_type` on the `PlanItem`, dispatched against one
lightweight, shared rule set (`content/skills/varthaai-generic-content.md`
— a fraction of the length of the reel skills, since there's no unique
creative judgment to encode here, just format rules + the Brand Kit for
voice):

| `content_type` | Output shape | Extra context needed beyond the topic |
|---|---|---|
| `poster_occasion` | One-liner or minimal — max 2–3 lines | none — topic is enough |
| `poster_event` | Still short, but must state the concrete specifics | what/date/time/venue-or-link/CTA — Planner or admin supplies these on the `PlanItem`, never inferred |
| `blog` | Long-form, per Brand Kit's "Blog voice" section (§2) for structure/length | topic (+ optional angle/keywords) |
| `reel_<series>` | Exactly per that series' own skill (§1 table) — unchanged | whatever that skill's "Needs from Ajwad" column already says |

This replaces the earlier plan to author two separate bespoke skills
(`varthaai-usual-poster`, `varthaai-blog`) — those were overbuilt for
what's actually a length/structure rule, not distinct creative judgment.
`PlanItem` needs a `content_type` field (Planner sets it when seeding the
plan) and, for `poster_event` specifically, a plain-text context/notes
field is enough to carry the event details for v1 — no rigid structured
schema needed.

**Blog AI Assistant**: still exists and deployed (`marketing/ai.py`,
`stream_completion`, `BlogDraftSession`/`BlogDraftMessage`, SSE into the
blog editor, `deploy/BLOG_AI.md`) but it's a generic per-turn chat with no
Brand Kit awareness — per your call, not good enough to build on, and
**slated for eventual retirement**. Once the generic-content path above
is live and Blog output is going through it, retire the old assistant
(§11) — don't rip it out mid-migration.

---

## 2. Brand Kit — lives on `core.Brand`, editable anytime

Per `poster-generation-plan.md` §2 for the content itself (Identity, Visual
identity, Layout conventions, Audience & voice, Asset inventory), but
**not** a static repo file — you asked for it to be part of Brands setup
and editable later, which also makes more sense given `core.Brand` already
exists as a real, working model (`core/models.py`) with its own admin page
(`templates/admin/brands.html` + `core/brands_views.py`, currently a plain
table + Bootstrap modal, super-admin only).

**Change**: add `brand_kit = models.TextField(blank=True)` (markdown) to
`core.Brand`, plus a lightweight audit trail —
`brand_kit_updated_at`/`brand_kit_updated_by` (FK `AdminUser`) — so it's
clear when it last changed and by whom, matching the FK convention used
elsewhere in this codebase (e.g. `StockAlert.acknowledged_by`).

**UI**: a "Brand Kit" tab/section on the Brand's edit view (the existing
`brands.html` currently only has an Add/Edit modal for the tabular fields —
brand kit content is long-form, so it gets its own dedicated page reached
from the brands table, e.g. `admin/brands/<id>/kit/`, not another modal
field). A large markdown textarea + save button is enough for v1 — no
live preview needed. Every content agent loads `brand.brand_kit` fresh
from the DB per run (never cached), same "load fresh every time" principle
as the original file-based plan, so an edit takes effect on the very next
run with no redeploy.

Feeds **three** consumers: poster generation, blog writing, and background
context injected into every Copywriter Agent run (tone/voice consistency
even though each series skill has its own tone rules). Extend the
poster-plan's suggested sections with:

- **Blog voice**: how it differs from reel scripts (longer-form, can be more
  explanatory/SEO-aware, still not corporate) — a few example openings you
  like.
- **Confidentiality rule for Verdict**: brand names of reviewed competitors
  are internal-only, never public — worth stating once in the kit so any
  agent touching Verdict content respects it by default.

Write the first version by hand (you + me, one working session) in
Phase 0 — do this before building anything else, since the Planner's
occasion research and the Copywriter's blog drafts are only as good as
this doc — but "one-time" only describes when v1 is *written*, not a
constraint on the system: it's a normal editable field from the start.

---

## 3. Why the Copywriter Agent is an orchestrator, not a writer

Three of the four series skills open with "ask Ajwad for X if missing" —
that's fine in a live chat, but a daily Celery beat task has no one to ask
in real time. If the Copywriter Agent only ran skills, most days it would
have nothing to write.

So the Copywriter Agent's real daily job is:

1. **Look ahead** at each series' prep-timing rule (§1 table) and find
   `PlanItem`s whose prep day is today or tomorrow.
2. **Check whether required inputs exist yet** (per-series input checklist,
   §1 last column).
3. **If inputs are missing**, don't generate a broken script — this should
   already show up as an open `ActionItem` (the Planner files these ahead
   of the deadline, see §5), but the Copywriter double-checks and nags
   again if the deadline is now imminent and still unmet.
4. **If inputs exist**, run the matching skill inside a `claude` CLI
   session (WebSearch/WebFetch **enabled** — unlike `debugger`, which
   blocks them; Learn/Verdict/Inside treat live verification as a
   correctness requirement, not an optional nicety), produce a `Script`
   draft, and open a review `ActionItem`.

This reframes "Copywriter Agent runs daily" correctly: it runs daily, but
most of what it does on most days is chase inputs and check deadlines, not
write.

---

## 4. Admin-approval gates — every stage

Confirmed: **Plan → Script → Poster**, each needs explicit sign-off before
the next agent acts on it. A plan already approved can still be
*improvised* on later Planner runs, but a proposed change sits as a diff
awaiting separate approval — it never silently overwrites an approved plan.

Concretely: every stage-output model (`ContentPlan`, `Script`,
`PosterAsset`) carries a `status` with at least
`draft → needs_review → approved / changes_requested`, and the next agent
in the chain only ever reads `approved` rows as its input.

---

## 5. Pending Tasks / Action Required

New concept, doesn't exist elsewhere in the codebase (checked — no
notification/task model currently). Single model, `content.ActionItem`,
is what both "an input is needed" and "something needs your review"
resolve to, so the dashboard is one query, not four:

```
ActionItem
  plan_item        FK → PlanItem (nullable — plan-level items have no single PlanItem)
  plan             FK → ContentPlan (nullable)
  kind              input_needed | plan_review | script_review | poster_review
  title             short, e.g. "Verdict prep — need product + your scores"
  description       longer context (what specifically, why, deadline reasoning)
  payload           JSONField — structured intake data once fulfilled
                     (product name, scores, uploaded photos, topic text, etc.)
  due_date          date — when this blocks the pipeline if unresolved
  status            open | done | dismissed
  resolved_by       FK AdminUser, nullable
  resolved_at        nullable
  created_at
```

Surface as:
- A dashboard widget (top of `admin/content-dashboard.php`-equivalent):
  count + list of open items, sorted by `due_date`.
- A dedicated "Pending Tasks" list page — the natural home for "reply with
  this week's Varthaanm story" or "approve September's plan."

The **Verdict input intake is the richest case** — it needs file uploads
(ingredient list / nutrition panel photos, product photos), not just text,
so `payload`/the intake form needs image attachment support, not a plain
text reply.

---

## 6. Series continuity (replacing Claude.ai's personal memory)

`varthaai-varthaanm-script` references a Claude.ai memory file
(`/areas/varthaai-varthaanm.md`) to recall the last episode's recap beat.
That memory system is personal to your claude.ai account and not something
a Django-scheduled agent can read. Replacement: add a `recap_summary`
TextField to `Script`. After an episode's script is **approved** (not
draft — matches the skill's own "don't log a draft he might still change"
rule), the Copywriter Agent writes a short recap into that field. The next
Varthaanm run reads the most recent approved Script for that series and
passes its `recap_summary` into the skill's context in place of the memory
file.

Same pattern reusable for Varthaai Inside if later episodes want continuity
across interviews (not required by the current skill, but the field is
general enough to serve it if needed).

---

## 7. System home: `content` Django app

Mirrors `debugger`'s shape (Celery + Django models + admin review UI) —
the only other place this codebase already runs an AI agent in
production, so this keeps the two consistent instead of inventing a
second pattern.

**Agent execution mechanism**: neither app uses the `claude_agent_sdk`
Python library. Both shell out to the **`claude` CLI** in headless mode
(`claude -p "<prompt>" --dangerously-skip-permissions`), via a shared
Redis-backed concurrency utility (`core/claude_cli.py`) that caps the
whole system to **2 concurrent `claude` processes** at a time — `debugger`
is being migrated to this now (separate in-flight change); `content`
adopts the same utility from day one rather than reintroducing the SDK.
Practically: `run_planner_monthly`/`_daily`, `run_copywriter_daily`, and
`run_designer_daily` (§7 tasks.py) each end up as one or more
`run_claude_cli(...)` calls, gated through that shared slot limiter, with
skill text (§1) injected via the prompt/`--mcp-config`/`--append-system-prompt`
mechanism the utility exposes — see the debugger migration for the exact
calling convention once it lands, and match it rather than inventing a
second one.

```
content/
  models.py
    ContentSeries        # seeded rows: Varthaanm, Verdict, Learn, Inside, Blog, Occasion
                          # cadence config: weekday/alternate-week rule, format,
                          # prep_lead_days, intake_lead_days, skill_slug
    ContentPlan           # one per month: status draft/needs_review/approved,
                          # approved_by, approved_at
    PlanItem              # one per planned post: plan FK, series FK (nullable=ad-hoc),
                          # planned_date, working_title/topic, status
                          #   (planned -> needs_input -> ready_for_script ->
                          #    scripted -> needs_approval -> approved -> posted / skipped)
    TrendFlag              # admin-submitted: url/text/note, flagged_at, plan_item FK nullable
    Script                 # plan_item FK (1:1), skill_used, raw_output, structured_json,
                          # recap_summary, version, status, reviewed_by/at, review_notes
    PosterAsset            # plan_item FK, brief JSONField, inspiration FK (from
                          # poster-generation-plan.md's PosterInspiration), version, status
    ActionItem              # see §5

  skills/                 # the 5 reel-series SKILL.md files + food-quality-analyst/
                          # references/, staged verbatim, plus one new lightweight
                          # varthaai-generic-content.md (§1) for posters/blog — text
                          # fed into the `claude -p` prompt
                          # via core.claude_cli, not reimplemented as Python logic

  agents/
    planner.py            # core.claude_cli.run_claude_cli(...) call — monthly seed
                          # run + daily trend/occasion-aware nudge run
    copywriter.py          # orchestrator described in §3 — WebSearch/WebFetch ON
    designer.py            # implements poster-generation-plan.md's pipeline;
                          # only for PlanItems with format=poster (Learn With
                          # Varthaai + Occasion) — Varthaanm/Verdict/Inside never
                          # reach this agent, they end at an approved Script

  tasks.py                # Celery beat entry points, mirrors debugger/tasks.py's
                          # shape (status transitions, SoftTimeLimitExceeded
                          # handling, never silently swallow a failure)

  views.py / urls.py       # admin panel: Content Calendar, Plan review, Script
                          # review, Poster review, Pending Tasks, Verdict History
```

### Celery beat additions (alongside the existing `poll-open-prs` entry)

```python
CELERY_BEAT_SCHEDULE = {
    'poll-open-prs': {...},  # existing, debugger
    'content-planner-monthly': {
        'task': 'content.tasks.run_planner_monthly',
        'schedule': crontab(day_of_month=28, hour=6, minute=0),
    },
    'content-planner-daily': {
        'task': 'content.tasks.run_planner_daily',
        'schedule': crontab(hour=6, minute=30),
    },
    'content-copywriter-daily': {
        'task': 'content.tasks.run_copywriter_daily',
        'schedule': crontab(hour=7, minute=0),
    },
    'content-designer-daily': {
        'task': 'content.tasks.run_designer_daily',
        'schedule': crontab(hour=7, minute=30),
    },
}
```

Times staggered so each agent reads the previous one's freshly-approved
output same-day if needed; adjust once real run durations are known
(Verdict's regulatory research in particular can run long — reuse
`debugger`'s `SoftTimeLimitExceeded` handling pattern).

---

## 8. On-demand trigger + bootstrapping September

Your original framing was admin-initiated: *"User comes and asks to make a
content plan for Varthaai for a specific time."* The design above is
purely cron-driven (28th monthly + daily nudges) — that misses the actual
entry point, and it breaks the POC in the near term:

- Today is **Sun 13 Sept 2026**. The monthly cron first fires **28 Sept**,
  generating *October*'s plan.
- **Varthaai Verdict launches Wed 23 Sept** — the flagship new series —
  10 days out, and would never get planned under a 28th-only trigger.
- Varthaanm's next episode is Fri 18 Sept; its prep day (the Sunday
  before) is **today**.
- September falls entirely in the gap. Nothing is plannable, reviewable,
  or demoable for two weeks.

**Fix**: add an admin-triggered action — "generate/update the plan for
[date range]" — that runs the same Planner logic as the monthly cron, just
invoked on demand with an explicit period instead of "next calendar
month." The 28th cron becomes just the *automated* invocation of this same
path, not a separate mechanism. This is how you bootstrap the rest of
September right now: trigger it manually for 13–30 Sept, review/approve,
and the pipeline is live in time for the Verdict launch and this Friday's
Varthaanm prep — instead of waiting for the first cron fire.

Add to `content/views.py`: a "Generate plan" action on the Content
Calendar page taking a start/end date, calling the same
`content.agents.planner` entry point the beat task calls.

---

## 9. `claude` CLI config differences from `debugger`

Both run through the same `core.claude_cli.run_claude_cli(...)` utility
(§7) and the same 2-slot concurrency cap, but with different tool/access
config passed in:

| | `debugger` | `content` |
|---|---|---|
| WebSearch/WebFetch | blocked | **enabled** (Learn/Verdict/Inside require live verification) |
| Write/Edit tools | blocked entirely (read-only RCA) | not needed — agents only write to their own Django models via app code, not arbitrary file edits |
| Skills | none | the 5 staged skills' text, injected per-run based on which `PlanItem` is due |
| DB access | read-only role, SQL tool via MCP | normal Django ORM from within the Celery task itself (the agent's *output* — the CLI call's stdout/JSON — is parsed and written by app code after the process returns, same shape as `debugger`'s `propose_fix` tool pattern — not raw SQL access for the agent) |
| MCP servers | `varthaai_debugger` (db_query_ro, read_logs, propose_fix, record_learning) | none needed for v1 — content agents work purely from prompt text + skill text + WebSearch, no custom tool server |

---

## 10. Verdict history — Django only, with a dashboard

Confirmed: retire the Google Sheet the skill was originally designed to
paste into; `Script.structured_json` (the skill's JSON block, extended with
Design/Pricing/Taste/Ingredients scores) is the system of record. Add a
**Verdict History** admin page: table of past Verdict episodes — internal
product name (never shown publicly), four sub-scores, final score,
publish status — sortable/filterable. This is a straightforward read view
over `Script` rows where `plan_item.series = Verdict`.

---

## 11. What's explicitly OUT of scope for v1

- **Automated Instagram/X trend scraping.** Confirmed deferred — v1's
  "trend awareness" is `TrendFlag` (you manually drop in a link/note) plus
  the Planner's festival/occasion calendar research. A real social-listening
  integration is its own later phase.
- **Publishing automation.** The pipeline ends at an approved `Script` +
  `PosterAsset` (or just `Script` for the three reel series, which need
  human filming/editing regardless). No auto-post to Instagram/X in v1.
- **Retiring the old Blog AI Assistant.** `marketing/ai.py`'s
  `BlogDraftSession` flow keeps running until Blog is going through the
  generic-content path (§1) — don't pull it out mid-migration. Actual
  removal (deleting the SSE view/models/deploy config in `deploy/BLOG_AI.md`)
  is a v1 cleanup item once Phase 2's Blog step is live, not a blocker
  for building that step.

---

## 12. Dashboard UI Plan

Reuses the live admin design system (`static/css/dash-style.css` —
dark sidebar, `.stat-card`/`.data-card`, pill status badges, `.btn-icon`,
`.filter-bar`) rather than inventing a new look; this drops into
`admin/base.html` as another set of pages, same as `brands.html`,
`b2b-dashboard.html`, etc. Mockup of the main landing page (real tokens/
components, illustrative data):

**https://claude.ai/code/artifact/83109609-53cf-4b93-8d9d-1731b9d12296**

**Page inventory:**

- **Content Dashboard** (the mockup) — landing page. Stat-card row
  (planned this month / awaiting your input / awaiting your review /
  posted this month), a **Pending Tasks** panel (the flagship widget —
  every open `ActionItem`, sorted by due date, one-click into the right
  form), a **This week** panel (per-series status at a glance using the
  same status vocabulary as `PlanItem.status`), and a recent
  scripts/posters table. Primary action in the header is **"Generate plan
  for…"** — the on-demand trigger from §8, not buried in a sub-page.
- **Content Calendar** — month grid (or grouped list) of `PlanItem`s,
  color-coded by series (same dot-legend as the mockup's table), click a
  day/item to open its review modal. Filter bar: series, status, month.
- **Pending Tasks** (full page, mockup's panel is the "top 4" preview) —
  every open `ActionItem`, filterable by kind/status/due date. Each kind
  opens a matching form: input-needed → intake form (text + file upload
  for Verdict's product photos/labels); plan/script/poster-review → the
  relevant review view with approve/edit/reject actions, per §4's gates.
- **Script review** (modal or dedicated view, opened from Pending Tasks
  or the table) — shows `skill_used`, the formatted output (VO + b-roll
  for Varthaanm, phrase card for Learn, Q&A sheet for Inside, talking
  points for Verdict), approve/request-changes actions, version history
  if regenerated.
- **Poster review** — per `poster-generation-plan.md` §7: generated image
  + brief, approve / regenerate / "edit with instruction" free-text box.
- **Verdict History** (Phase 5, §10) — table of past episodes: internal
  product name, four sub-scores, final score, publish status. Shown but
  greyed out with a "Phase 5" tag in the sidebar until that phase lands
  (as in the mockup), so the nav doesn't silently disappear/reappear
  later.
- **Brand Kit** — not its own top-level nav item; lives one level under
  **Brands** (§2), reached from the brands table, since it's brand-scoped
  data, not a content-pipeline artifact.

---

## 13. Build order

**Phase 0 — Foundations**
1. Write the Brand Kit's first version (§2) by hand, saved into the new
   `Brand.brand_kit` field.
2. Scaffold `content` app: models (§7), seed `ContentSeries` rows with the
   §1 cadence table.
3. Admin pages: Content Calendar (month view of `PlanItem`s), Pending
   Tasks list, Brand Kit tab on the Brand edit view.
4. Confirm `core.claude_cli.run_claude_cli(...)` (from the debugger
   migration, §7) is available and working before wiring any content
   agent to it — this is a shared dependency, not something `content`
   builds itself.

**Phase 1 — Planner Agent**
1. On-demand trigger first (§8): admin picks a date range, Planner runs
   immediately. This is what bootstraps September — use it to plan
   13–30 Sept (Varthaanm prep, the Verdict launch on the 23rd) before the
   28th cron ever fires.
2. Monthly run: seed the month's `PlanItem`s for the four fixed-cadence
   series + web-researched festival/occasion slots + blog slots →
   `needs_review` → you approve/edit via the Content Calendar. Same code
   path as the on-demand trigger, just cron-invoked with "next month" as
   the period.
3. Daily run: ingest open `TrendFlag`s, propose changes to *remaining*
   days only, never touching already-passed or already-approved-and-locked
   items without a fresh explicit approval.
4. Daily: scan upcoming `PlanItem`s against each series' `intake_lead_days`
   and file `ActionItem`s (input_needed) ahead of the deadline.

**Phase 2 — Copywriter Agent (everything except Verdict)**

Confirmed order: Varthaanm, Learn With Varthaai, Varthaai Inside, usual
posters, and Blog all come before Verdict.

1. Wire the orchestrator (§3) for **Varthaanm** first (simplest — no file
   uploads, no external research needed beyond the story you give it).
2. Add **Learn With Varthaai** and **Varthaai Inside**.
3. Author the shared `varthaai-generic-content.md` rule set (§1 table) and
   wire `content_type` dispatch through the orchestrator — this covers
   **occasion posters, event posters, and Blog** in one pass, not three
   separate skills. Blog output lands as a `Script` draft against
   `PlanItem`, reviewed same as everything else, written into
   `marketing.Blog` on approval. Once this is live and proven, retire
   `marketing/ai.py`'s old `BlogDraftSession` SSE assistant (§11) — not
   before.

**Phase 3 — Designer Agent**
Wire `poster-generation-plan.md`'s pipeline for Learn With Varthaai +
Occasion posters only, gated on an approved `Script`/`PlanItem`, running
2 days before publish.

**Phase 4 — Polish**
Unified Pending Tasks page, review-diff UI for Planner's "proposed changes
to an approved plan," recap-summary wiring (§6) verified across a real
multi-episode run, Content Calendar month view.

**Phase 5 — Varthaai Verdict** (confirmed last)
Wire `varthaai-verdict-script` + `food-quality-analyst`, the
file-upload intake form (product photos, ingredient/nutrition labels —
richer than any other series' `ActionItem`, §5), and the Verdict History
dashboard (§10). Until this phase lands, Verdict episodes (launching
23 Sept, well before this phase is reached) are scripted manually via the
skills directly, same as today — the Planner still reserves the calendar
slots from Phase 1 onward (§1 build-order note).

**Phase 6 (deferred, not v1)**
Real IG/X trend monitoring, publishing automation.

---

## 14. Admin panel integration & permissions

Two corrections to the mockup (§12): it's not a separate app with its own
sidebar rail, and access isn't all-or-nothing.

### 14.1 Sidebar — folds into the existing single sidebar

Real structure, from `templates/admin/base.html` + `core/context_processors.py`
(not the mockup's standalone dark rail — that was for showing the
component styling, not the actual information architecture): one sidebar,
one `<nav>`, gated per-item by `{% if '<module>' in nav_perms %}`, where
`nav_perms` is computed in `admin_context()` from `AdminUser.brand_permissions`.

Today, Marketing-ish items aren't actually grouped — Coupons/Reviews sit
under the generic "Menu" label, Blogs sits under "General," there's no
"Marketing" section label at all despite `marketing` being its own Django
app. Proposed change: add a `<p class="nav-section-label">Marketing</p>`
group (same pattern as the existing "B2B" and "General" labels) containing
Coupons, Reviews, Blogs, **and** the new Content items — Content Dashboard,
Content Calendar, Pending Tasks, Scripts, Posters, Verdict History (Phase
5, shown-but-tagged like the mockup). This is a small, reversible template
change — flag if you'd rather leave Coupons/Reviews/Blogs where they are
and only group the new items.

### 14.2 Permission keys — one per section, mirroring the existing mechanism

No new permission *mechanism* needed — `core/api.py`'s
`has_module_permission(user, brand_id, module)` already does exactly
"super_admin bypasses everything; everyone else needs `module` (or
`'all'`) in `brand_permissions[brand_id]`." Every existing page opts in by
setting `permission_module = '<name>'` on its view (e.g.
`BlogAISessionAPI.permission_module = 'blogs'`) and appears in the sidebar
only if that name is in `nav_perms`. Content pages get the same treatment,
one module name per section — this **is** the "each section needs
separate access" behavior, not a new thing to design:

| Page | `permission_module` |
|---|---|
| Content Dashboard (landing) | `content_dashboard` |
| Content Calendar + "Generate plan" trigger | `content_calendar` |
| Pending Tasks | `content_tasks` |
| Scripts (list + review) | `content_scripts` |
| Posters (list + review) | `content_posters` |
| Verdict History (Phase 5) | `content_verdict` |

Add all six to `core/context_processors.py`'s `NAV_MODULES` list (currently
`['dashboard', 'orders', 'coupons', 'flavors', 'packs', 'customers',
'reviews', 'b2b', 'stocks', 'expenses', 'blogs']`) so `nav_perms` covers
them, and set the matching `permission_module` on each `content` app view.
A non-super-admin sees/reaches only the sections explicitly granted in
their `brand_permissions[brand_id]` array (or all of them via `"all"`),
exactly like every other module today.

**Brand Kit needs no new key.** It lives under Brands (§2), and
`core/brands_views.py` already hard-gates the whole Brands page to
`is_super_admin` — so brand-kit editing inherits that, consistently with
Brand management already being super-admin-only.

---

## 15. Phase 0 implementation checklist (concrete)

**Status: done**, built directly against `main` (not a worktree — pure
scaffolding, low risk). What landed, against the checklist below:

- `core.Brand` — `brand_kit`/`brand_kit_updated_at`/`brand_kit_updated_by`
  fields + migration `core/migrations/0002_brand_brand_kit_*.py`.
- `core/brands_views.py` — `brand_kit_page` + `BrandKitAPI` (GET/POST),
  same super_admin-only gate as the rest of that file. Linked from a new
  book icon in the Brands table's Actions column.
- `templates/admin/brand-kit.html` — functional (load + save), mirrors
  `settings.html`'s form conventions.
- `content` app scaffolded by hand (`manage.py startapp` refused — the
  directory already existed from staging the skill files): `models.py`
  (all 6 tables from §7/§5), `apps.py`, `urls.py`, `views.py`,
  migrations `0001_initial` + `0002_seed_series` (seeds the 6
  `ContentSeries` rows from §1's table — verified in the DB, weekday/
  prep_lead_days/skill_slug all correct per series).
- Three admin pages, permission-gated: Content Studio (dashboard),
  Content Calendar, Pending Tasks — all render honest empty states (no
  `PlanItem`s exist yet; that's Phase 1). The dashboard's "Generate plan"
  trigger from §8 was deliberately **not** built here — out of Phase 0's
  scope, shows an explanatory note instead of a fake button.
- `core/context_processors.py` `NAV_MODULES` extended with
  `content_dashboard`/`content_calendar`/`content_tasks` only —
  `content_scripts`/`content_posters`/`content_verdict` (§14.2) stay
  undeclared until Phase 2/3/5 actually build those pages, so there's
  never a permission key for a page that doesn't exist yet.
- `templates/admin/base.html` — new "Marketing" nav-section-label
  holding the 3 new items. **Did not** move the existing
  Coupons/Reviews/Blogs items into it (§14.1 flagged this as a separate,
  reversible call) — they're still under "Menu"/"General" as before; say
  the word if you want them regrouped too.

**Verified live** (Django test client, not just `check`): super_admin
sees and can load all 3 pages + Brand Kit; a restricted admin granted only
`content_dashboard`/`content_calendar` gets a 403 on `/admin/content/tasks/`
(page *and* API) and the sidebar genuinely omits that link — confirmed by
inspecting the rendered HTML, not just the permission function in
isolation. Any test admin users created for this check were deleted
afterward. `python manage.py check` is clean; `core`/`content` migrations
applied successfully against the real dev DB.

**Resolved**: `ContentPlan.brand` (FK → `core.Brand`, `on_delete=CASCADE`,
same convention as `Order`/`B2BCompany`/etc.) added via a hand-written
migration (`content/migrations/0003_contentplan_brand.py` — the
`content_plans` table was empty at the time, so Django's interactive
"provide a one-off default" prompt couldn't run headless; written by hand
instead, verified with `makemigrations --check --dry-run` reporting no
drift). `PlanItem` deliberately has **no** `brand` field of its own — it
reaches brand through `plan__brand`, one FK hop, not a duplicate. Every
Phase 0 API (`ContentDashboardAPI`, `ContentCalendarAPI`, `PendingTasksAPI`)
now filters by `current_brand_id(request)`, the same helper every other
brand-scoped view in this codebase uses. `ActionItem` has no `brand` field
either — it reaches brand via `plan__brand` (plan-level items) or
`plan_item__plan__brand` (everything else), unioned with a `Q` filter
(`_action_items_for_brand()`). **Verified live**: created a second brand
+ a plan/item/action-item under brand 1, switched the active-brand session
between the two, confirmed brand 2 sees zero rows and brand 1 sees exactly
its own — then deleted all the test data.

Original checklist (for reference — now the "what to build" list above
reports "done" against each):

1. **Brand Kit field** (§2)
   - `core/models.py`: add `brand_kit = models.TextField(blank=True)`,
     `brand_kit_updated_at = models.DateTimeField(null=True, blank=True)`,
     `brand_kit_updated_by = models.ForeignKey('accounts.AdminUser',
     null=True, blank=True, on_delete=models.SET_NULL)` to `Brand`.
   - `python manage.py makemigrations core`.
   - `core/brands_views.py`: add a view + route for the edit page (e.g.
     `brand_kit_page(request, pk)` + a `BrandKitAPI` POST action), gated
     the same way the rest of `brands_views.py` already is
     (`is_super_admin` check, no new permission key per §14.2).
   - `core/urls.py`: `path('brands/<int:pk>/kit/', brands_views.brand_kit_page, name='brand_kit')`.
   - New template `templates/admin/brand-kit.html` (extends `admin/base.html`,
     large textarea + save button — mirror `templates/admin/settings.html`'s
     form conventions rather than inventing new ones).
   - Write the first draft of the kit content by hand (you + me) and save
     it through the new page once it exists.

2. **`content` app scaffold**
   - `python manage.py startapp content`; register in `Varthaai/settings.py`
     `LOCAL_APPS`.
   - `content/models.py`: `ContentSeries`, `ContentPlan`, `PlanItem`,
     `TrendFlag`, `Script`, `PosterAsset`, `ActionItem` (§7, §5) —
     `Script`/`PosterAsset` reference `content.skills`/
     `poster-generation-plan.md`'s models but don't need those models
     built yet for the schema itself to exist.
   - `python manage.py makemigrations content`; data-migration or
     `apps.py.ready()`-time seed for the `ContentSeries` rows (§1's
     cadence table — Varthaanm/Verdict/Learn/Inside/Blog/Occasion).
   - `content/urls.py` (`app_name = 'content'`), wired into
     `Varthaai/urls.py` as `path('admin/', include('content.urls'))`,
     same line-shape as every other app there.
   - `content/apps.py`: plain `AppConfig`, no `ready()` signal needed yet
     (unlike `debugger`'s worker-restart recovery — nothing async exists
     in `content` until Phase 1's Celery tasks land).

3. **Admin pages (views + templates), permission-gated per §14.2**
   - `content/views.py`: `content_dashboard_page`, `content_calendar_page`,
     `pending_tasks_page` (+ matching DRF API views for each, `HasModulePermission`
     + the module names from §14.2's table).
   - Templates: `templates/admin/content-dashboard.html`,
     `content-calendar.html`, `content-tasks.html` — extend `admin/base.html`,
     reuse `.stat-card`/`.data-card`/`.pill` classes directly from
     `dash-style.css` (no new CSS needed — confirmed by the §12 mockup).
   - `core/context_processors.py`: extend `NAV_MODULES`; `templates/admin/base.html`:
     add the "Marketing" section-label group + the new nav items (§14.1),
     `{% if 'content_dashboard' in nav_perms %}` etc.

4. **Verify**
   - `python manage.py check` clean.
   - Log in as `super_admin`: all new nav items visible, pages load
     (empty states — no `PlanItem`s yet).
   - Log in as a non-super-admin test user with a `brand_permissions`
     array missing `content_tasks`: confirm that nav item is hidden *and*
     the URL 403s directly (not just hidden in the sidebar) — matches
     how every other module's permission check already behaves.

Phase 1 (Planner Agent) is the next slice after this, and its concrete
steps depend on merging the `claude_cli` migration branch first — write
those once that's decided.

---

## 16. What can start in parallel, right now

Dependency check across phases — three tracks need nothing from each
other and can start today:

- ~~**Track A — Phase 0**~~ **done** (§15).
- ~~**Track B — Designer Agent's core pipeline**~~ **mostly done** — see §17.
- **Track C — pure content, no code**: writing the Brand Kit's first
  draft (§2) and the shared `varthaai-generic-content.md` rule set (§1) —
  much shorter than the 4 reel skills (format rules, not creative
  judgment), can happen anytime. **Not started.**

~~**Blocked, in order**: Phase 1 needs Track A's models *and* the
`claude_cli` branch (§15) merged.~~ **The `claude_cli` migration branch
merged into `main`** (fast-forward, no conflicts with Track A/B's
uncommitted work) — see §18. Phase 1 (Planner Agent) is now **done**, see
§18. Phase 2 needs Phase 1 (`PlanItem`s to act on) — next up. Phase 5
(Verdict) is structurally parallelizable with Phase 2 — same orchestrator
pattern, same dependency — but is deliberately sequenced last by your own
call (§1 build-order note), not a hard technical blocker; worth knowing
if priorities shift. Phase 4 needs 1/2/3 all done.

---

## 17. Track B status — Designer Agent's core pipeline

**Two real corrections to the original poster-generation-plan.md**, made
while implementing:

- **Storage: local Django media, not S3.** This codebase doesn't use S3
  anywhere (CLAUDE.md: "File uploads | Django media files") — the S3
  assumption was carried over from zip-backend, a different codebase.
  `PosterInspiration.image` / `BrandAsset.image` are plain `ImageField`s,
  same as `Brand.logo`.
- **Captioning + brief generation use direct `anthropic` calls, not
  Gemini.** Both are non-agentic (text/vision in, text out, no tools) —
  same shape as `debugger`'s `consult_advisor` — so they don't need to
  wait on Gemini credentials *or* the `claude_cli` migration. **Gemini is
  reserved for the one thing only it can do here: generating the actual
  image.**

### What's built

- **Models**: `PosterFormat` (shared choices: `1:1`/`9:16`/`4:5`),
  `BrandAsset` (the asset library — logo/product/lifestyle/team photos,
  tagged), `PosterInspiration` (the reference library, per poster plan
  §4.1). `PosterAsset` (from Track A) gained a real `inspiration` FK
  (replacing the placeholder `inspiration_id` int) and a `format` field.
  Migrated cleanly — `content/migrations/0004_*.py`.
- **`content/agents/designer.py`**:
  - `infer_topic_category(topic)` — keyword-rule categorizer (festival/
    announcement/event/quote/general), not an LLM call, matching
    zip-backend's `infer_sale_type` approach per the original plan.
  - `select_poster_inspiration(brand, topic, format)` /
    `list_eligible_inspirations(...)` — the 3-layer fallback from poster
    plan §4.3 (strict category+format → format-only → any active row),
    random pick within the matching set.
  - `generate_poster_brief(brand, topic, inspiration, context_notes)` —
    direct Anthropic call, dynamic field list (only asks `logo_position`
    if `inspiration.has_logo`, only asks `cta_text` if context given, per
    poster plan §5's "never let the brief-writer skip a field that
    matters").
  - `generate_poster_image(...)` — the Gemini call. Structured per poster
    plan §6 (reference + topic asset + logo + text prompt, exact pixel
    dimensions per format, the "don't look like AI art" closing line),
    never raises (best-effort, always returns a debug dict per poster
    plan §11).
- **`ingest_poster_inspirations` management command** — folder-of-images
  → auto-captioned `PosterInspiration` rows, subfolder name becomes
  `topic_category` (poster plan §4.2's `--axis` trick), idempotent
  re-runs keyed on `(brand, source_label)`.
- **New settings**: `CONTENT_TEXT_MODEL` (reuses `ANTHROPIC_API_KEY`),
  `GEMINI_API_KEY`/`GEMINI_POSTER_MODEL` (both unset — see below). Added
  `google-genai` to `requirements.txt`, flagged unverified-version same as
  this file's existing `litellm`/`AI_ASSIST_MODELS` TODOs — no PyPI
  access in this session to confirm the pinned version or model id.

### Verified live, not just `check`

- `select_poster_inspiration()` against the real (empty) table — returns
  `None` gracefully, no crash.
- `generate_poster_brief()` — real Anthropic round-trip (fake in-memory
  brand/inspiration objects, no DB writes), got back a correctly
  structured brief with every required field committed to, `logo_position`
  included because the fake inspiration had `has_logo=True`.
- `ingest_poster_inspirations` — full path against a synthetic test image
  (generated with Pillow, not a real poster): captioned, parsed, written
  to a real `PosterInspiration` row, then `select_poster_inspiration()`
  found it via a strict category+format match. **Caught and fixed a real
  bug this way**: Claude wrapped its JSON response in a ` ```json ` fence
  despite the system prompt saying not to — `json.loads` failed silently
  into the fallback path (raw text as description). Added
  `_strip_code_fence()` and re-ran the same live test to confirm the fix;
  second run produced clean `description`/`design_language`/`tags`.
  Test data (DB rows + files) deleted after each check.

### What's NOT verified — genuinely can't be, in this environment

`generate_poster_image()` (the Gemini call) has never actually run — no
`GEMINI_API_KEY` and the `google-genai` package isn't installed here.
Before relying on it:
- Confirm the `google-genai` pinned version and the exact
  `client.models.generate_content(...)` calling convention against
  current docs — written from memory, not verified against a live SDK.
- Confirm `gemini-3-pro-image-preview` (or whatever's current) actually
  supports multi-image-input editing the way this code assumes (reference
  + topic asset + logo composited together).
- Run it once against a real `GEMINI_API_KEY` with a real ingested
  inspiration before trusting the output.

### Not done (deferred, not blocking)

- **Versioning/edit endpoint** (poster plan §7 — regenerate / "edit with
  instruction"): `PosterAsset.version` exists on the model, but no code
  writes a second version yet — natural next slice once a first real
  image generation has been verified, not before.
- **Admin review UI** for posters: correctly out of scope here — that's
  Phase 3 (§13), after Phase 1/2 exist to gate on. Track B was
  deliberately pipeline-only per §16's own framing.
- **Real inspiration library**: zero real Varthaai posters ingested yet
  (only the synthetic test image, deleted). `select_poster_inspiration()`
  will keep returning `None` for every real topic until
  `ingest_poster_inspirations` runs against actual past posters.

---

## 18. Phase 1 status — Planner Agent + manual trigger

Triggered by: "How can I create a calendar" → clarified as "we should
allow admins to manually trigger any agent." Since only the Designer's
pipeline existed at that point (no Planner, no Copywriter), a generic
"trigger any agent" panel would have been mostly dead buttons — you chose
to merge `claude_cli` and build the real Planner instead of a stopgap
manual-entry form.

### The merge

The CLI-migration work (§9/§15's blocker) existed but had never been
committed — found live on disk in the `worktree-agent-ab4c15542f841b46d`
worktree (`core/claude_cli.py`, `debugger/mcp_server.py`,
`debugger/guard_hook.py`, `debugger/agent.py` rewritten off the SDK, plus
`requirements.txt`/`settings.py`/deploy doc updates). Reviewed file-by-file,
committed on that branch, fast-forward merged into `main` — 0 conflicts
with Track A/B's uncommitted session work despite both touching
`settings.py`/`requirements.txt` (stashed the session's WIP first, merged,
popped the stash — auto-merged cleanly). `manage.py check` and the full
test suite (70 tests) pass post-merge.

### What's built

- **`content/agents/planner.py`** — `generate_plan(plan)`. Like Designer's
  brief generation, this is a **direct Anthropic call, not the `claude`
  CLI** — it needs no filesystem/DB/web tool access, since all context
  (brand kit, series cadence, existing plan items, ~90 days of post
  history, flagged trends) is fetched by our own code and handed to it in
  the prompt. Returns a JSON array of proposed items; **only creates
  `PlanItem`s for `(planned_date, content_type)` slots not already present
  in the plan** — re-running is additive-only, so an admin-reviewed plan
  is never silently rewritten (§1's rule). `reel_verdict` is hard-excluded
  both in the prompt and in code, honoring the "Verdict last" build order.
- **`content/tasks.py`** — `run_planner_task(plan_id)` (same idiom as
  `debugger/tasks.py`: set a transient status before dispatch, catch
  `SoftTimeLimitExceeded`/`Exception`, always land on a stable status with
  the error surfaced on the row) and `seed_next_month_plans` (the beat
  task wired into `CELERY_BEAT_SCHEDULE` for the 28th of every month —
  **the same task function** the manual trigger uses, so an admin-
  triggered run and the automated one behave identically, per §8).
- **`ContentPlan.Status.GENERATING`** + `generation_error` field (new
  migration `0005`) — lets the calendar page show "Generating…" and poll,
  and surfaces a failure instead of leaving a plan silently stuck.
- **`PlannerTriggerAPI`** (`POST /admin/api/content/planner/trigger/`) —
  the actual "Generate plan for [period]" trigger from §8. Gets-or-creates
  a `ContentPlan` for the given period, refuses to re-trigger one already
  `generating` or re-touch one already `approved`, dispatches
  `run_planner_task.delay()`. Gated on the same `content_calendar` module
  permission as the calendar page itself (didn't invent a separate
  permission key for a trigger with no page of its own behind it).
  `ContentCalendarAPI` now also returns a `plans` list (id/period/status/
  error) alongside `items`, so the frontend can poll.
- **`content-calendar.html`** — "Generate Plan" button opens a modal
  (defaults to next month), POSTs to the trigger endpoint, then polls
  `ContentCalendarAPI` every 3s while any plan is `generating` and stops
  once it resolves. This is the actual answer to the original "how can I
  create a calendar" question.
- **`content/agents/_util.py`** — `strip_code_fence()` factored out of
  `ingest_poster_inspirations.py` (Track B) since the Planner needed the
  identical fence-stripping logic; both now share one copy.

### A second real bug, caught live (not hypothetical)

First live test of `generate_plan()` failed: `json.loads` raised
"Unterminated string" — the JSON was truncated mid-object. Root cause,
confirmed by inspecting the raw response: **`claude-sonnet-5` engages
extended thinking by default even when the caller never asked for it** —
one run spent 3702 of a 4096 `max_tokens` budget on thinking alone,
leaving almost nothing for the actual JSON. Fix: pass
`thinking={'type': 'disabled'}` explicitly. Re-tested live — clean JSON,
`stop_reason: end_turn`, 0 thinking tokens.

This is a systemic risk, not a one-off — `designer.py`'s
`generate_poster_brief()` and `ingest_poster_inspirations.py`'s `_caption()`
make near-identical direct-Anthropic calls with small `max_tokens` budgets
(1024 each) and had never hit this failure mode in their own earlier live
tests, purely because those particular prompts happened not to trigger
heavy thinking. Both now also pass `thinking={'type': 'disabled'}`
defensively, before it bites in production on a harder prompt. (Out of
scope to touch here: `debugger/agent.py`'s `_call_advisor` explicitly
requests `thinking={'type': 'adaptive'}` on purpose — that one *wants*
reasoning — but it shares the same `max_tokens=4096` ceiling and has not
been independently re-verified against this same truncation risk.)

### Verified live

- `generate_plan()` against a real 1-week period for brand `Varthaai`:
  correctly placed `reel_inside` on the Monday (weekday cadence), skipped
  Wednesday entirely (Verdict exclusion), placed `reel_varthaanm` on the
  Friday, and proposed sensible occasion/learn/blog items with concrete
  `context_notes` — not filler. **Idempotency verified**: re-running the
  same plan created 0 new items (all slots already filled).
- `run_planner_task` success path (`.run()`, bypassing the broker):
  `GENERATING → NEEDS_REVIEW`, `generation_error` cleared, items created.
- `run_planner_task` failure path (mocked `generate_plan` to raise):
  `GENERATING → DRAFT`, `generation_error` set to the exception text.
- Full HTTP round-trip via `django.test.Client` with a real session
  (`force_login` + `BRAND_SESSION_KEY`, not the ORM directly): missing
  dates → 400; valid trigger → 200 + `plan_id`; re-trigger while
  `generating` → 400 "already being generated"; `ContentCalendarAPI`
  correctly reflects the `generating` status. All test data cleaned up
  after each check.
- `manage.py check` clean; full existing test suite (70 tests, unrelated
  to this feature — no `content` app tests exist yet) still passes
  post-merge.

### Not done (deferred, not blocking)

- **No automated tests for `content/`** — everything above was verified
  via live one-off shell scripts, not a committed `content/tests.py`
  suite. Worth adding before this ships past a POC.
- **Copywriter/Designer manual triggers**: deliberately not added.
  Copywriter doesn't exist yet (Phase 2); Designer's pipeline exists
  (Track B) but a trigger button for it would still be mostly dead today
  — no real `PosterInspiration` library ingested and no `GEMINI_API_KEY`
  configured, so image generation would fail every time. The pattern
  established here (task + transient status + trigger API + polling UI)
  is the one to reuse once those pieces exist, not a new one to invent.
- ~~`ActionItem` isn't created when a plan lands in `needs_review`~~ —
  fixed: `run_planner_task` now creates a `PLAN_REVIEW` `ActionItem` (only
  when it actually created items, and `get_or_create`-guarded so re-runs
  don't duplicate an already-open one), so a generated plan now surfaces
  on the Pending Tasks page per §5's original requirement.

---

## 19. "No service uses the Anthropic API" — routing everything through `claude -p`

Triggered by: "Let's make planner agent also claude -p no service should
use ANTHROPIC API", right after `claude login` succeeded in this session —
the intent is to authenticate every Claude call off the CLI's own login
session, never `ANTHROPIC_API_KEY`. §18 (Phase 1) had deliberately kept
Planner/Designer/captioning on direct `anthropic` SDK calls, reasoning
they were non-agentic and didn't need `core.claude_cli`; this section
reverses that — non-agentic doesn't mean "skip the CLI", it means "give
the CLI zero tools".

### The gate, checked first

Before converting anything: does headless `claude -p` actually authenticate
without `ANTHROPIC_API_KEY`? Verified live, twice — once with the bare
`-p` flags, once with the exact flag set `run_claude_cli` uses
(`--output-format stream-json --setting-sources '' --dangerously-skip-
permissions`), both with `ANTHROPIC_API_KEY` explicitly unset
(`env -u ANTHROPIC_API_KEY`). Both succeeded; the CLI's own init event
reported `"apiKeySource":"none"`. Gate passed — everything below follows.

### The silent-defeat gotcha, closed

`run_claude_cli` builds `run_env = dict(os.environ)` — so a key present
anywhere upstream (`.env` → `load_dotenv()` → `os.environ` → subprocess
env) would leak through and the CLI would silently prefer API billing over
the login session, defeating the goal without erroring. **Fixed**:
`core/claude_cli.py` now unconditionally strips `ANTHROPIC_API_KEY` from
the subprocess env after the `env=` merge, with no opt-out — verified by
mocking `subprocess.run` and asserting the key is absent from the captured
env kwarg even with a real key configured in settings.

### What changed

- **`core/claude_cli.py`** — added the strip above; added a shared
  `NO_TOOLS` constant (every built-in tool name denied) so every zero-tool
  caller uses the same list instead of each keeping its own copy.
- **`content/agents/planner.py`** (`generate_plan`) — converted from a
  direct `anthropic.Anthropic().messages.create()` call to
  `run_claude_cli(prompt, disallowed_tools=NO_TOOLS, max_turns=1)`. The
  `thinking={'type': 'disabled'}` fix from §18 doesn't carry over —
  `run_claude_cli` exposes no thinking parameter, and re-testing the JSON
  output live showed no recurrence of the truncation issue on this path.
- **`content/agents/designer.py`** (`generate_poster_brief`) — same
  conversion, same pattern.
- **`content/management/commands/ingest_poster_inspirations.py`**
  (`_caption`) — **not a like-for-like swap**: `claude -p` prompts are
  plain text with no inline-image content block, unlike the Anthropic
  API's `{"type": "image", ...}`. Converted to grant the CLI `Read` access
  to the image file on disk (`allowed_tools=['Read']`,
  `disallowed_tools=` every other built-in) and prompt it to read the file
  at its absolute path, rather than base64-encoding the image into the
  request. `base64`/`MEDIA_TYPES` are gone from this file.
- **`debugger/agent.py`** (`_call_advisor`) — converted from a direct
  Anthropic call to a **second** `run_claude_cli` call with `NO_TOOLS`.
  This one is nested: it runs inside `debugger/mcp_server.py`'s
  `consult_advisor` tool, itself a child process of an OUTER `claude` run
  that already holds one concurrency slot — the advisor call needs a
  SECOND slot concurrently. **`CLAUDE_CLI_MAX_CONCURRENT` must stay >= 2**
  or this self-deadlocks (the outer run blocks forever waiting for a slot
  it can never get while holding the only one itself); default is already
  2, documented at each of the three places this now matters
  (`core/claude_cli.py`, `debugger/agent.py`, `debugger/mcp_server.py`).
  Also removed `run_agent`'s explicit `env['ANTHROPIC_API_KEY'] = ...`
  injection — dead now that the key is stripped unconditionally anyway.
- **`debugger/tests.py`** — `ConsultAdvisorTests` rewritten to mock
  `debugger.agent.run_claude_cli` instead of `debugger.agent.anthropic`;
  dropped the now-meaningless "raises without API key" test, added one for
  "raises when the CLI isn't on PATH" instead. Net +1 test (70 → 71), all
  passing.
- **`deploy/DEBUGGER.md`** — corrected: it previously (this session)
  claimed headless runs "authenticate via `ANTHROPIC_API_KEY` in the
  subprocess environment... there is no interactive OAuth login step" —
  that was written before this was tested and turned out to be wrong.
  Rewritten to document `claude login` + stored credentials as the actual
  mechanism, with the parts genuinely not verified here (headless
  SSH-only login flow, credential re-auth cadence) called out rather than
  guessed at.

### Deliberately NOT converted

**`marketing/ai.py`'s Blog AI Assistant** still uses `litellm` with
`api_key=settings.ANTHROPIC_API_KEY` directly — found while grepping for
every remaining `ANTHROPIC_API_KEY` reference, not touched here. It's a
real-time SSE-streaming chat endpoint (`deploy/BLOG_AI.md`); converting it
to `claude -p` isn't a search-and-replace like the four call sites above —
headless CLI calls aren't naturally streaming, so it needs its own design
pass, not a rushed change alongside this one. It's also the assistant §1
already flagged as "basically a bad assistant" to be retired in favor of a
Brand-Kit-driven blog writer (Track C, not started) — the more likely
resolution is replacement, not in-place conversion. `ANTHROPIC_API_KEY`
stays in `settings.py`/`.env.example` for this reason; its comment there
now says explicitly what still depends on it.

### Verified live

- Planner: real `generate_plan()` call with `ANTHROPIC_API_KEY` explicitly
  unset (`env -u ANTHROPIC_API_KEY`) — created 5 correctly-varied items,
  proving the CLI path works end-to-end for this call, not just that it
  imports cleanly.
- The env-strip itself: mocked `subprocess.run`, called `run_claude_cli`
  with a real key configured in settings, asserted `ANTHROPIC_API_KEY`
  absent from the captured subprocess `env` kwarg — proves the guarantee
  holds even when a valid key IS present (the ambiguous case a success-
  only test can't rule out).
- Designer: real `generate_poster_brief()` call — produced a complete,
  correctly-structured brief (scene/mood/colors/style/headline/etc.),
  including the "no brand kit — flagging as a risk" behavior working as
  designed.
- Ingestion (the genuinely new Read-tool-based vision mechanism): ran
  against a synthetic test poster with known content (specific text,
  specific colors, no logo). The stored caption correctly transcribed the
  exact text, correctly identified the two-color-block layout, and
  correctly reported `has_logo: false` — confirms the CLI is actually
  reading the image file's pixels via Read, not hallucinating a generic
  poster description. Test image and DB row deleted after.
- `manage.py check` clean; full `debugger` + `content` suite (71 tests)
  passes.

---

## 20. Nano Banana Pro (Gemini 3 Pro Image) — wiring corrected, still needs a real key

Triggered by: "we wanna use nano banana pro." §17/§19 had already targeted
`gemini-3-pro-image-preview` for `generate_poster_image()`, flagged as
"written from memory, not verified against a live SDK." Used this request
to actually verify it, rather than just confirming the name.

### What was wrong

The existing `generate_poster_image()` call was missing `config=` entirely
— no `response_modalities`, no `image_config`. Per the current SDK docs,
without `response_modalities` explicitly including `"IMAGE"`, the model
can return text only. This was likely why the very first real run would
have silently failed to produce an image, independent of any account/key
issue. Aspect ratio was only ever a text instruction in the prompt
("Output exactly 1080x1080 pixels") — the SDK has a structured
`image_config.aspect_ratio` param instead, which is authoritative where a
prompt-text instruction is advisory at best.

**A genuine source conflict surfaced and was reconciled, not silently
picked**: two `WebFetch` calls against `ai.google.dev` doc pages both
independently returned a `client.interactions.create(model=..., input=...,
response_format={...})` shape — a different top-level API surface. That
didn't match the `client.models.generate_content(...)` pattern already in
this file. Cross-checked against the actual `googleapis/python-genai`
GitHub README, Google Cloud's Gemini 3 Pro Image docs, an official Google
Cloud cookbook notebook titled "Gemini 3 Pro Image (Nano Banana Pro 🍌)
Generation", and a community reference site — all four independently
converged on `models.generate_content(model=..., contents=...,
config=GenerateContentConfig(response_modalities=[...],
image_config=ImageConfig(...)))`, with images read back via
`response.candidates[0].content.parts[].inline_data` — which is exactly
what `_extract_image_bytes()` already did. Went with the converged,
higher-confidence source family; the `interactions.create` shape may be a
newer higher-level API but isn't what this file uses.

### What changed

- **`content/agents/designer.py`**: added `_FORMAT_ASPECT_RATIOS`
  (SQUARE→1:1, STORY→9:16, PORTRAIT→4:5 — all valid per the SDK's
  supported list) and wired `config=types.GenerateContentConfig(
  response_modalities=['TEXT','IMAGE'], image_config=types.ImageConfig(
  aspect_ratio=..., image_size='2K'))` into the `generate_content` call.
  Dropped the pixel-dimension text instruction from the prompt — the
  structured param replaces it.
- **`Varthaai/settings.py`**: `GEMINI_POSTER_MODEL` default changed from
  `gemini-3-pro-image-preview` to `gemini-3-pro-image` — confirmed this is
  now the GA/stable name (the `-preview` alias still works, same pricing,
  but the stable name is the one to default to going forward).
- **`requirements.txt`**: `google-genai` bumped `1.3.0` → `2.23.0` —
  confirmed directly against PyPI (not a guess this time) and matches the
  `ImageConfig`/`response_modalities` API this code now calls.
  **Also removed `anthropic==0.121.0`** — grepped the whole codebase and
  confirmed nothing imports the `anthropic` package any more (§19 moved
  every direct-API caller onto `claude -p`; the Blog AI assistant uses
  `litellm`, not this package) — a genuinely dead dependency, not a
  drive-by removal.
- **New dependency conflict surfaced and documented, not silently
  resolved**: `google-genai==2.23.0` requires `httpx>=0.28.1`;
  `litellm==1.55.8` (Blog AI assistant) declares `httpx<0.28.0`. Verified
  empirically that `litellm` still imports and `litellm.completion` stays
  reachable under `httpx==0.28.1` (both now installed together in this
  venv), but did NOT verify the Blog AI assistant's actual SSE streaming
  path under this combination — that's a live production feature this
  session didn't test. Deliberately did NOT bump `litellm` to resolve it
  properly (current PyPI is `1.100.1`, a 45-release jump from what's
  pinned) — flagged in a comment on `requirements.txt` instead of guessed
  at. **This needs a real decision + test pass before the next deploy
  installs from a clean venv.**

### Verified live — the actual gate, closed

The user added a real `GEMINI_API_KEY`. First live call hit `403
PERMISSION_DENIED — Gemini API has not been used in project ... or it is
disabled` — an account-level gap (Generative Language API not enabled on
the Google Cloud project), not a code bug: the pipeline built the request
correctly, made a real network call, got a real structured error back, and
handled it exactly as designed (never raised, returned `None` + the full
error in `debug['error']`). User enabled the API; re-ran the exact same
call — **real image returned, 2.6MB, 2048x2048**.

That re-run surfaced one more real, previously-undocumented fact: **Gemini
3 Pro Image returns `image/jpeg` by default, not PNG** — confirmed by
inspecting `inline_data.mime_type` directly on a live response. Every
mention of "png_bytes" in this codebase (`generate_poster_image`'s return-
value naming, `_image_part`'s hardcoded `mime_type='image/png'` for
reference-image input) was wrong. Fixed: `_extract_image_bytes` now
returns `(bytes, mime_type)` instead of just bytes, `debug['mime_type']`
carries the real value through to any caller, and `_image_part` derives
the mime type from the actual file extension via `mimetypes.guess_type`
instead of assuming PNG for stored reference/logo images too.

### DesignerTestAPI — a real admin-dashboard test panel

Added because the user asked "how do I test from the admin dashboard" —
there was no UI for this at all before, only shell scripts. `POST
/admin/api/content/designer/test/` (gated on the existing `content_dashboard`
permission) runs the full pipeline synchronously — select inspiration,
generate brief, generate image — and returns the brief text, full debug
dict, and the image as base64, persisting nothing (no PosterAsset, no
PlanItem needed). A card on `content-dashboard.html` (topic/format/context
inputs, a Generate button, inline image preview, collapsible debug JSON)
wraps it. Deliberately NOT the real Phase 3 review UI — no versioning, no
approval, no persistence — just "does the pipeline work right now",
reusable for retesting whenever the Gemini/model config changes.

---

## 21. Plan review UI — from "just a listing" to per-item approval

Triggered by two follow-ups in the same session: "How can I review it?"
(asked right after successfully generating a real plan from the admin UI
— exposed that no review/approve UI existed at all, only a read-only
table), then "Each Plan should be togglable and each plan item needs
approval. Right now, it's just a listing with no proper visibility. Make
it very intuitive and simple" (a direct correction to the first pass).

### First pass (superseded)

Built a flat "Plan items" table across all plans at once, a separate
"Plan periods" table with one whole-plan "Approve" button, and a per-item
skip/unskip icon toggle. Functionally correct but exactly what the user
then called out: no per-plan grouping, no visibility into how many items
were decided, and no way to approve an item individually (`PlanItem`
already has a real `APPROVED` status value in its model — the first pass
never used it, treating "not skipped" as the only signal).

### Redesign

- **Accordion, one panel per plan** — replaces the two disconnected
  tables. Each panel header shows the period, a status badge, and an
  always-visible **count summary** ("N approved · N skipped · N pending")
  without needing to expand — directly answers "no proper visibility".
  Newest plan opens by default; others collapsed but one click away.
  Vanilla JS + CSS, no accordion library — matches "keep it simple", and
  Bootstrap's own accordion component would have fought the custom
  per-item content more than it helped here.
- **Per-item review is a genuine 3-way toggle**, not a single skip
  checkbox: PLANNED (undecided, default) <-> APPROVED <-> SKIPPED, one
  click each way, click the active button again to undo. Two icon
  buttons per item (check = approve, ban = skip), colored solid when
  active so decided-vs-pending is visible at a glance without reading
  text; the whole item row also gets a subtle green/grey tint once
  decided.
- **"Approve Plan" redefined as the bulk-finish action**, not the only
  way to approve: it bulk-approves whatever's still PLANNED (items the
  admin already individually approved or skipped are left exactly as
  set) and locks the plan as a whole (`ContentPlan.status -> APPROVED`,
  `approved_by`/`approved_at`, resolves the `PLAN_REVIEW` ActionItem) —
  so an admin can either decide every item by hand or fast-path everything
  left with one click at the end.
- Backend: `ContentCalendarAPI.post` now dispatches `approve_plan` |
  `toggle_item_approve` | `toggle_item_skip` (renamed from the first
  pass's `skip_item`/`unskip_item`). `GET` now also annotates each plan
  with `total_count`/`approved_count`/`skipped_count`/`pending_count` via
  a single aggregated query (`Count(..., filter=Q(...))`), not a Python
  loop per plan.

### Verified live

Ran the full toggle sequence against disposable test data (a throwaway
plan + 3 items, deleted after) — **not** the user's real generated plan,
after an earlier draft of this same verification was correctly blocked
by the permission classifier for being about to approve that real plan as
a side effect of testing. Confirmed: approve → toggle again reverts to
planned → re-approve; skip is independent and doesn't interfere with
approve; `GET`'s count aggregation matches DB state exactly
(1 approved / 1 skipped / 1 pending mid-review); `approve_plan` correctly
bulk-approves only the still-PLANNED item, leaves the already-skipped one
alone, and rejects a second approval attempt. `manage.py check` clean;
full `debugger` + `content` suite still passes.

### Follow-up: editing an item's content, and locking after approval

Triggered by: "How will I modify a plan item?" — there was no way to edit
a `PlanItem`'s own fields at all, only approve/skip its review status.

- **`ContentCalendarAPI.post` gains `update_item`** — edits
  `working_title`/`planned_date`/`content_type`/`context_notes`,
  independent of approve/skip status (editing doesn't reset a decision,
  same "each control does one thing" rule as the toggles).
  `content_type` is validated against `ContentSeries.ContentType.values`
  with `reel_verdict` excluded — same exclusion the Planner itself
  enforces, so an admin can't reintroduce Verdict through the edit form.
- **Real gap closed while building this**: nothing previously stopped an
  admin from still toggling/editing an item after its plan was already
  `APPROVED` (the UI didn't show it, but the API had no check). All three
  item-level actions (`toggle_item_approve`, `toggle_item_skip`,
  `update_item`) now reject once `plan.status == APPROVED`. UI reflects
  this too — an approved plan's items render as a read-only list with a
  "Locked" badge instead of action buttons.
- UI: a pencil icon added to each item row's action group, opening a
  shared "Edit Plan Item" modal (title/date/content-type/context-notes),
  populated from `data-*` attributes already rendered onto each row
  (avoids a second fetch). `content_type`'s `<select>` is built from the
  same `CONTENT_TYPE_LABELS` map the row display already used, filtered
  to exclude Verdict.

### Verified live

Full round-trip against disposable test data (created, tested, deleted —
not the user's real plans): a valid edit applied all four fields and left
`status` untouched; empty `working_title` rejected; `content_type:
reel_verdict` rejected with the exact valid-values list; after manually
approving the test plan, both `update_item` and `toggle_item_skip` on its
item were correctly rejected with the same "already approved" message,
and the title genuinely stayed unchanged in the DB. `manage.py check`
clean; full `debugger` + `content` suite still passes.

---

## 22. Series display names + Planner context_notes specificity

Two corrections in the same turn:

**Series names.** The calendar UI's `CONTENT_TYPE_LABELS` fallback map
(used in the edit-item dropdown, and as the series-column display when a
`PlanItem` has no linked `ContentSeries`) used shorthand — "Varthaanm",
"Learn", "Verdict" — instead of the real names ("Varthaai Varthaanm",
"Learn With Varthaai", "Varthaai Verdict"). Fixed to match
`content/migrations/0002_seed_series.py`'s actual seeded `name` values
exactly (also fixed "Inside" → "Varthaai Inside" for the same reason,
though not explicitly named — same pattern, would've been inconsistent
to leave it). Also aligned `ContentSeries.ContentType`'s Django choice
labels in `content/models.py` (dormant — nothing currently calls
`get_content_type_display()` — but the exact same mistake, worth fixing
now rather than leaving a second copy to drift). Required a cosmetic
migration (`0006`) since Django tracks `choices=` as field state even
though the label text isn't stored in Postgres.

**`context_notes` specificity.** Real problem, not cosmetic: live output
showed the Planner writing notes that described the SERIES FORMAT
("cover current news relevant to the brand", "an evergreen educational
angle", "no major festival falls here, use as a soft weekend") rather
than committing to one concrete, usable idea — exactly the failure mode
you'd expect from a generic "be specific" instruction with no worked
examples. Since `context_notes` is the actual brief the Copywriter Agent
(Phase 2, not yet built) will hand to a Claude Skill to write the real
script/poster/blog, vague notes here means every downstream generation
starts from nothing.

Fix: rewrote `build_planner_prompt` in `content/agents/planner.py` with
(a) an explicit list of banned hedge phrases ("e.g.", "such as",
"consider", "or" listing options instead of picking one — a "delete this
and it could say '[insert topic here]'" test), and (b) a worked bad/good
example for EACH content_type, since generic instructions weren't enough
— reel_varthaanm needs one real story beat, reel_inside needs 2-3 actual
interview questions, poster_learn needs the exact word/phrase + meaning
+ usage, blog needs a real thesis/claim, not a topic category.

**Verified live**: re-ran plan generation for the same period. Every
item's context_notes now reads as a genuine, usable brief — e.g.
poster_learn named the exact Kannada word "ಕುರುಕುರು" (kurukuru, meaning
crunchy) with pronunciation, a usage example, and even a specific visual
pairing suggestion; reel_inside wrote three real, answerable interview
questions about Nendran banana sourcing and a specific rejected supplier
batch; reel_varthaanm proposed one concrete story ("packing the first 50
bags by hand") with specific narrative beats, not a topic. Zero hedge
phrases across all 5 generated items. Test plan created and deleted, not
left in the DB. `manage.py check` clean; migration applied cleanly.

---

## 23. Regenerate a single plan item

Triggered by: "We need an option to regenerate a specific item also."
Until now, fixing a bad item meant either manually rewriting it
(`update_item`, §21) or living with it — no way to ask the Planner for a
genuinely different take on the same slot.

- **`content/agents/planner.py`**: `regenerate_item(item, instruction='')`
  — a second `claude -p` call scoped to ONE item. Keeps `plan`/
  `planned_date`/`content_type`/`series` fixed, asks only for a new
  `working_title`/`context_notes`. Optional `instruction` steers it (e.g.
  "make it about our sourcing process"); without one, the prompt asks for
  something genuinely different from the current version, not a trivial
  reword — the current title/notes are shown to the model specifically so
  it doesn't just paraphrase them back. The large context_notes-quality
  block from §22 was factored out into a shared `_CONTEXT_NOTES_GUIDANCE`
  constant so both prompts enforce the identical bar rather than risking
  two copies drifting apart.
- **Status is deliberately reset to `PLANNED` on regenerate**, regardless
  of what it was before — a real, considered difference from
  `update_item`, which leaves status alone. The reasoning: `update_item`
  is the admin's own typed edit, so an existing approval still reflects
  their intent; `regenerate_item` produces content the admin hasn't seen
  yet, so any prior approve/skip decision no longer means anything and
  must be re-made after reading the new version.
- **`ContentCalendarAPI.post`** gains `regenerate_item`, added to the
  same plan-lock guard the other item actions already use (§21) — can't
  regenerate an item in an approved plan either.
- UI: a new "Regenerate" icon (rotating arrows) per item row, opening a
  small modal with an optional "what would you like different?" textarea
  — reuses the edit modal's visual pattern. Shows a loading state
  (`showLoader`) since this is a real ~10-30s Claude call, same as the
  Designer test panel.

### Verified live

Created a disposable test item, pre-set it to `approved` with a
deliberately generic title/notes ("Generic Blog Topic" / "Write about
seasonal eating"), then regenerated it with the instruction "make it
about how our chips are made without palm oil." Result: a genuinely
specific, on-instruction brief (coconut oil vs. palm oil, framed as a
sourcing-integrity story, not a marketing checkbox) — confirms the
instruction is actually followed, not ignored. Status correctly flipped
from `approved` back to `planned`. Regenerating on a subsequently-locked
plan was correctly rejected with the same message the other item actions
use. Test data deleted after. `manage.py check` clean; full `debugger` +
`content` suite (71 tests) still passes.

---

## 24. Phase 2 (Copywriter Agent) and Phase 3 (Designer Agent) — built in parallel

Triggered by: "Apply phase 2 and 3 parallely using multiple agents." Both were
genuinely unbuilt at the integration level (Phase 1's Planner and Track B's
Designer *core pipeline* existed; nothing dispatched a skill against a due
`PlanItem`, nothing gated poster generation on an approved `Script`, and
neither had a review UI). Two agents ran concurrently against the same
working tree, each scoped to a disjoint file set — new `views_*.py`/`urls_*.py`/
templates/tests per agent, and a shared "do not touch" list (`content/models.py`,
`content/tasks.py`, `content/urls.py`, `content/views.py`,
`core/context_processors.py`, `templates/admin/base.html`,
`Varthaai/settings.py`, `Varthaai/urls.py`) reserved for a single integration
pass afterward — to avoid the two agents clobbering each other on Celery
task wrappers, URL wiring, nav permissions, or (if either had judged one
necessary) a migration.

**The one real coupling between the phases**, resolved up front rather than
left for the two agents to independently guess at: for poster content types,
the Copywriter's approved `Script` IS the actual on-poster copy (headline +
subline + any CTA), not an abstract "script" — so the Designer's brief-writer
must not invent its own headline independently of what the admin already
approved. Given to the Designer agent as a fixed contract; it implemented an
`approved_copy` param on `generate_poster_brief` that locks the brief to the
approved text (trim-only, never rephrase). The Designer agent also didn't
wait on the Copywriter's actual code landing — it built and verified against
its own disposable, hand-created `Script` rows, so the two genuinely didn't
block each other.

### Phase 2 — `content/agents/copywriter.py`

The orchestrator described in §3: most days it has nothing to draft, its
real job is checking whether a due `PlanItem`'s required input actually
exists before generating anything.

- `due_plan_items()` — gates on **both** `plan.status == APPROVED` and
  `item.status == APPROVED` (not just the item's own toggle, which can be
  provisional inside a still-`NEEDS_REVIEW` plan — see §21), excludes
  `reel_verdict` (Phase 5) and items that already have an `APPROVED` `Script`.
- Per-content-type input-readiness check (blank/placeholder `context_notes`
  → files an `INPUT_NEEDED` `ActionItem`, get-or-create so reruns don't
  duplicate it, sets `PlanItem.status = NEEDS_INPUT`) before ever calling
  the CLI.
- Skill dispatch — `reel_varthaanm` → `varthaai-varthaanm-script`,
  `reel_inside` → `varthaai-inside-questions`, `poster_learn` →
  `learn-with-varthaai-script`, `poster_occasion`/`poster_event`/`blog` →
  `varthaai-generic-content.md` (`reel_verdict` never dispatched). Skill
  text goes in as `system_prompt`; `run_claude_cli` runs with
  **WebSearch/WebFetch enabled** (§3/§9 — live verification is a
  correctness requirement for these series) and Write/Edit/Bash denied — the
  CLI never touches the DB directly, this module parses its text output and
  writes the `Script` row itself.
- Varthaanm recap continuity (§6): reads the most recent *APPROVED*
  Varthaanm `Script`'s `recap_summary` for the same brand, asks the model to
  end its response with a machine-parseable `RECAP_FOR_NEXT_EPISODE:` line,
  stores it on the new (draft) `Script` for the *next* run to read once
  this one is approved.
- **Status-transition scheme** (the contract Phase 3's gate relies on):
  `PlanItem.status == APPROVED` is deliberately overloaded — "approved as an
  idea, no script yet" pre-`due_plan_items` vs. "script approved" post-review
  — disambiguated only by whether an `APPROVED` `Script` exists.
  `NEEDS_INPUT` → `NEEDS_APPROVAL` (script drafted) → back to `APPROVED`
  once `content/views_scripts.py`'s `ScriptsAPI.approve` fires (also
  resolving the `SCRIPT_REVIEW` `ActionItem`). Regenerating a `Script`
  creates a new version and resets `PlanItem.status` to `NEEDS_APPROVAL`
  (the admin hasn't read the new draft yet — same reasoning as
  `planner.regenerate_item`, §23).
- **Known gap, flagged not fixed**: an item that goes `NEEDS_INPUT` doesn't
  self-heal once `context_notes` is later filled in — `due_plan_items` only
  reads `status == APPROVED`, so re-surfacing it is a later-phase concern.
- Review UI: `content/views_scripts.py` (`ScriptsAPI` — list/detail/
  `approve`/`request_changes`/`regenerate`, brand-scoped, rejects
  acting on a stale (non-latest) `Script` version) +
  `templates/admin/content-scripts.html` (list + detail/review modal,
  mirrors the plan-review page's approve/regenerate UX) at
  `/admin/content/scripts/`, permission module `content_scripts`.

**Verified live**: real `claude -p` round trips for `reel_varthaanm` (two
episodes back-to-back, second one's draft call genuinely read the first's
`recap_summary` from a real DB row) and for the generic-content path
(`poster_occasion`), plus a live `regenerate` through the API. 33 tests in
`content/tests_copywriter.py` (parsing helpers, the due-items gate, the
readiness check, `INPUT_NEEDED` filing + non-duplication, `ScriptsAPI`'s
full action set, page permission gating) — all passing individually and in
combined runs. All against disposable data, deleted after. `manage.py check`
clean; zero edits to any file the pre-existing test baseline exercises.

### Phase 3 — `content/agents/designer.py` (extended)

Builds on Track B's core pipeline (§17/§20: `select_poster_inspiration`,
`generate_poster_brief`, `generate_poster_image` already existed and worked)
with the piece that was actually missing — gating, persistence, versioning,
and review.

- `generate_poster_brief(..., approved_copy=None)` — new optional param
  (backward-compatible; the existing `DesignerTestAPI` never passes it and
  is unaffected). When given, splices an "APPROVED COPY" block into the
  prompt plus a rule forbidding the brief-writer from rephrasing it —
  implements this section's coupling contract above.
- `run_daily(brand=None)` — eligible `PlanItem`s: poster content type +
  a latest-by-version `Script` that's `APPROVED`. Skips one whose latest
  `PosterAsset` is `NEEDS_REVIEW`/`APPROVED` (already in the queue or done)
  **or has no image at all** — a failed Gemini call deliberately counts as
  "already attempted" so a bad key/outage can't retry-storm every run; only
  `CHANGES_REQUESTED` (or no attempt yet) allows a fresh automatic try. Every
  attempt persists a `PosterAsset` (even a failed one, with the failure
  recorded in `generation_metadata`) so nothing vanishes silently
  (poster-generation-plan.md §11). Format defaults: `poster_learn`/
  `poster_occasion` always square; `poster_event` square unless
  `context_notes` hints at a different aspect ratio.
- `regenerate_poster(plan_item, instruction='', inspiration_id=None)` —
  poster-generation-plan.md §7's edit loop: new `PosterAsset` version, reuses
  `generate_poster_image`'s existing `extra_instruction` plumbing, optional
  different inspiration (brand-validated).
- Review UI: `content/views_posters.py` (`PostersAPI` — list/detail/
  `approve`/`request_changes`/`regenerate`/`retry`, same staleness and
  brand-scoping discipline as the Scripts API) + `templates/admin/
  content-posters.html` (image preview in the detail view, regenerate modal
  with an optional instruction + inspiration picker) at
  `/admin/content/posters/`, permission module `content_posters`.
- **Shared-file gap noted, not fixed**: `PosterAsset` has no
  `review_notes`/`reviewed_by`/`reviewed_at` (unlike `Script`) —
  `request_changes` notes are stashed in `generation_metadata` for now,
  functional but a second `request_changes` call overwrites the first note.
  Would need a migration; deferred rather than made unilaterally mid-parallel-run.

**Verified live**: `GEMINI_API_KEY` confirmed genuinely configured;
`run_daily()` against a disposable approved `Script` produced a real
1,073,398-byte JPEG (mime type correctly detected as `image/jpeg`, not
assumed PNG), brief text genuinely reflected the approved Kannada copy and
brand-kit colors, an immediate rerun correctly skipped, and
`regenerate_poster(instruction=...)` produced a real version-2 image with the
instruction applied. 34 tests in `content/tests_designer.py` (the
`approved_copy` splice, every format-resolution case, the full gating
matrix, versioning, cross-brand validation, `PostersAPI`'s full action set)
— all passing. Full project suite re-run at 132/132 mid-build. Caught and
fixed three of its own bugs before declaring done: tests were leaking real
tiny image files into the live `media/content/posters/` directory (Django's
`TestCase` doesn't sandbox `MEDIA_ROOT` — fixed with a `tempfile`-based
override, ~10 already-leaked files deleted); a test urlconf omitted the
`accounts` namespace and broke `base.html` rendering; one test had the wrong
expected status code for an error path. All disposable data and media
cleaned up.

### Integration pass (orchestrator, after both agents finished)

Both delivered exactly within their file scope (`git diff --stat` showed
only `content/agents/designer.py` modified plus each agent's own new
files — confirmed neither touched the other's or the forbidden shared
files). What the two `urls_*.py` fragments couldn't do themselves — because
two Django `include()` calls under the *same* explicit namespace don't both
reverse (`namespace_dict` keeps only the first-registered urlconf per
namespace; the second `include()`'s names silently fail to reverse rather
than erroring at `check` time, only a `urls.W005` warning surfaces it) — was
folding `urls_scripts.urlpatterns`/`urls_posters.urlpatterns` into
`content/urls.py`'s own list (still `app_name = 'content'`, one urlconf, one
namespace) rather than adding two more `include()` lines in
`Varthaai/urls.py`. Also done: `content.tasks.run_copywriter_daily`/
`run_designer_daily` (thin wrappers around each agent's `run_daily()`, same
try/except/log shape as `run_planner_task` but with no per-object status to
flip since a sweep's per-item failures are already recorded by the agent
itself); `CELERY_BEAT_SCHEDULE` entries at 07:00/07:30 (after the existing
06:00 plan-seed slot); `content_scripts`/`content_posters` added to
`core/context_processors.py`'s `NAV_MODULES`; sidebar links (Scripts,
Posters) added to `templates/admin/base.html`'s Marketing section.
Verified: `manage.py check` clean (the namespace warning gone after the
`urls.py` fix), all seven new/existing `content:*` url names reverse
correctly, full project test suite re-run after every integration edit.

---

## 25. Phase 5 — Varthaai Verdict

Triggered by: "start phase 5 Varthaai Verdict." The flagship series —
launched 23 Sept, per §1's build-order note — was still fully manual: the
Planner hard-excluded `reel_verdict` from every plan it generated, and no
code dispatched `varthaai-verdict-script` at all. This section wires it up:
Verdict slots now get reserved on the calendar automatically, a rich
file-upload intake form collects what only Ajwad can supply (a physically
bought/tasted/photographed competitor product), and the Copywriter runs
`varthaai-verdict-script` — which itself invokes `food-quality-analyst` —
in one headless session once that intake is complete.

### The one real design decision: how "invoke a skill from a skill" works headless

`varthaai-verdict-script`'s Step 1 is "Invoke the food-quality-analyst
skill" — meaningful in an interactive Claude.ai session where skill
invocation is a first-class action, meaningless as written for a single
`claude -p` call with one `system_prompt`. Resolved by concatenating BOTH
skills' full text (frontmatter stripped, same as every other dispatch) plus
food-quality-analyst's two reference docs (`regulatory-sources.md`,
`scoring-rubric.md` — only 176 lines combined, inlined rather than granting
filesystem access to them) into one `system_prompt`
(`copywriter._load_verdict_system_prompt`), so the model has everything
food-quality-analyst's own Step 1 tells it to check, in the same session,
without any tool-mediated cross-skill call.

### `VerdictIntake` — the richest ActionItem (§5)

New model (migration `0008`), one row per `PlanItem`: `product_name`
(internal-only, never public — the reel films with the brand hidden),
category/market/price_point, three score+notes pairs (Design/Pricing/
Taste, Ajwad's own judgment), and two label fields that each accept EITHER
a photo OR typed text (`ingredients_label_photo`/`ingredients_text`,
`nutrition_label_photo`/`nutrition_text`) — never both required.
`content/agents/copywriter._verdict_intake_missing_fields` is the single
completeness check, reused by both the readiness gate and (implicitly) the
intake form's own "what's still missing" framing.

### Dispatch — `content/agents/copywriter.py`

- `due_plan_items()` — the `reel_verdict` exclusion is gone; it goes
  through the identical plan/item-approval + prep-deadline gate as every
  other content_type. `run_daily_nudge`'s exclusion (planner.py) was
  deliberately KEPT — nudging a verdict slot on a flagged trend is
  meaningless, its content comes from Ajwad's intake, not a Planner angle.
- `_is_ready()` gained a `reel_verdict` branch: no `VerdictIntake` row at
  all → "no product intake submitted yet"; an incomplete one → names
  exactly which fields are still missing. Both file the same
  `INPUT_NEEDED` `ActionItem` the generic path already uses — self-healing
  applies here too (§4's fix): once the intake is completed, the very next
  Copywriter run drafts it.
- `_run_verdict_and_save()` — the actual dispatch: `_load_verdict_system_prompt()`
  as `system_prompt`, `build_verdict_prompt()` (structured intake data, not
  context_notes — Verdict's real brief is Ajwad's scores/notes, not
  anything the Planner writes) as the prompt, tools
  `allowed_tools=['Read', 'WebSearch', 'WebFetch']` — Read is new here,
  reusing the exact "grant Read, prompt for the absolute path" pattern
  already proven in `ingest_poster_inspirations.py`'s `_caption()`, so the
  model reads the actual label photos off disk rather than working from a
  typed description of them. No `max_turns` cap — food-quality-analyst's
  research workflow is many web_search/web_fetch/Read calls before it
  writes a word.
- `_extract_last_json_block()` — `varthaai-verdict-script`'s output has
  THREE sections (talking-point sheet, sheet-row line, JSON block); the
  existing `strip_code_fence` (built for a single-fenced-block response)
  doesn't apply. Takes the LAST ` ```json ` fenced block in the text (the
  skill's own Step 5 always produces it last), falling back through earlier
  blocks if the last one fails to parse, `{}` if nothing does — never
  raises, so a malformed response still leaves a reviewable `Script`
  (`raw_output` has everything) rather than losing the draft.
- `_persist_script()` (shared by every content_type, not verdict-specific)
  gained a connection-health check — see "A real bug this surfaced" below.

### Planner — `content/agents/planner.py`

`reel_verdict` is no longer excluded from `build_planner_prompt`/
`generate_plan`; instead it's given an explicit rule: propose Wednesday
slots per the series' normal cadence, but working_title/context_notes must
be the EXACT placeholder text ("Varthaai Verdict — <date>" /
"Awaiting product selection and intake from Ajwad…") — never an invented
product. **Defense in depth**: `generate_plan` doesn't just instruct this
and trust the model — it force-overwrites whatever the model actually
returned for any `reel_verdict` item with the exact placeholder,
regardless of content, so a model that ignores the instruction (verified
live with a deliberately misbehaving mocked response — see Verification)
still can't leak an invented competitor name onto the calendar.

### Intake form + Verdict History — `content/views_verdict.py` (new file)

- `VerdictIntakeAPI` (multipart, `MultiPartParser`/`FormParser`) at
  `/admin/content/verdict/intake/<plan_item_id>/` — GET current state (or
  an empty shape), POST partial-or-full saves. Deliberately NOT gated on
  completeness at save time (that's `_is_ready`'s job) — Ajwad should be
  able to save progress mid-form. A file field is only overwritten when a
  NEW file is actually present in the request, so a partial re-save (e.g.
  just fixing a typo in `product_name`) never wipes an already-uploaded
  label photo.
- `VerdictHistoryAPI` at `/admin/content/verdict/history/` — one row per
  `PlanItem`, its LATEST-version `Script` (any status). Reads
  `Script.structured_json` as the source of truth for every scored field
  (product name included) rather than `VerdictIntake` — a regenerated
  script's numbers are what's actually current, not the input that
  produced an earlier draft. `content_verdict` added to `NAV_MODULES` +
  a "Verdict History" sidebar link; the intake form itself has no nav
  entry (reached only via Pending Tasks' deep link, matching §12's
  "input-needed → intake form" spec) — `content-tasks.html`'s `taskUrl()`
  now special-cases `input_needed` + `content_type == 'reel_verdict'` to
  route there instead of the Content Calendar.

### A real bug this surfaced: stale DB connections across long external calls

Live-verifying the Verdict path (food-quality-analyst's web research
genuinely takes minutes, not seconds) hit
`psycopg.OperationalError: consuming input failed: SSL error: unexpected
eof while reading` on the very next DB write after the `claude -p` call
returned — the connection to this project's remote Neon Postgres had gone
idle-stale during the multi-minute subprocess wait. Not hypothetical: this
is exactly what a real `run_copywriter_daily` sweep would hit in
production once a `reel_verdict` item is due.

First fix attempt (`close_old_connections()`) was wrong and proved it live:
this project's `CONN_MAX_AGE` is Django's default `0`, which makes
`close_old_connections()` treat EVERY connection as "obsolete" and close it
unconditionally — harmless in a real request/task (Django reopens on next
query), but it broke a `TestCase`-wrapped live test on the very next run
(closed the TestCase's shared savepoint connection with no way for the
wrapper to reopen it — `OperationalError: the connection is closed`, a
*worse* failure than the one it was meant to fix). Corrected to a genuine
health check: `if not connection.is_usable(): connection.close()` in
`_persist_script` (shared by every content_type's dispatch, not just
Verdict's) — only intervenes when the connection is actually dead, a
no-op on a healthy one, verified to fix the live Verdict path and to leave
the previously-flaky `TestCase` live test passing cleanly on retry.

### Verified live (not just mocked)

- **Full pipeline, real data**: a synthetic label photo (Pillow-generated,
  with known text INCLUDING a deliberately-fake, sequentially-numbered
  FSSAI license number) run through `draft_script_for_item` — the model
  genuinely read the photo via the Read tool (transcribed "Raw Banana, Palm
  Oil, Salt, Turmeric Powder (INS 100)" correctly), ran real regulatory web
  research (cited FSSAI, flagged the fake license number's suspicious
  sequential pattern as likely placeholder data rather than trusting it),
  correctly folded the food-quality-analyst findings into a condensed
  Ingredients & Nutrition verdict, combined it with the given Design/
  Pricing/Taste scores into a `final_score` of 6.3 (average of
  6/6/7/6.0, rounded to one decimal — matches the skill's own formula),
  and set `controversial: true` on the transparency finding rather than
  silently smoothing it. `structured_json` parsed correctly;
  `PlanItem.status` → `needs_approval`; the Verdict History aggregation
  (`_history_row`) round-tripped the same data correctly. Disposable
  brand/intake/media deleted after.
- **Planner placeholder slots, real call**: `generate_plan` against a real
  2-week period produced two `reel_verdict` items, both on Wednesdays
  (2026-09-16 and 2026-09-23 — the real launch date), both with the exact
  required placeholder text, no invented product.
- **Defense-in-depth, mocked adversarial input**: a mocked Planner response
  that DID invent a product name/angle for `reel_verdict` was still forced
  back to the exact placeholder by `generate_plan`'s own code, not the
  model's good behavior.
- 30 tests in `content/tests_verdict.py` (intake completeness, the
  `_is_ready` branch, JSON extraction edge cases including a malformed
  final block falling back to an earlier valid one, the dual-skill dispatch
  call arguments — asserted directly on `system_prompt`/`allowed_tools`,
  not just the persisted result — intake API multipart save/partial-resave/
  cross-brand/wrong-content-type, history aggregation and brand scoping)
  plus one updated `tests_copywriter.py` test that previously asserted the
  now-removed exclusion. Full `content` app suite: 117/117 passing.

### Not done (deferred, not blocking)

- No manual "trigger Verdict draft now" button was added as part of this
  section — the self-healing `due_plan_items` gate (§4) already picks up a
  freshly-completed intake on the next scheduled sweep; a same-turn manual
  trigger for Scripts/Posters was added separately (see the codebase's
  current state — built alongside this work, not by this section).
- Regulatory-research *quality* (whether the specific FSSAI citations are
  actually correct, whether `fssai.gov.in`'s automated-fetch block degrades
  gracefully to `WebSearch` as the skill specifies) was not independently
  fact-checked — verified live that the mechanism fires (Read reads the
  real photo, WebSearch/WebFetch actually run, JSON parses), not that
  every regulatory claim in a given response is correct. That's a
  domain-expertise review, not something re-verifiable from this
  environment.

---

## 26. Manual "Draft Script" / "Generate Poster" triggers

Triggered by: "How can I generate content and poster for a plan item" —
investigation found that, despite Phase 2/3's full daily-sweep
infrastructure (§24) landing, there genuinely was no manual per-item
trigger anywhere in the API/UI at the time this question was asked
(§25's "Not done" note above anticipates this work but nothing in
`content/views_scripts.py`/`content/views_posters.py` implemented it —
confirmed by grep before writing anything). Both `run_copywriter_daily`
and `run_designer_daily` were correctly wired into `CELERY_BEAT_SCHEDULE`
(7:00/7:30 AM), but that only fires content once an item falls inside its
series' lead-time window — no way to force it on demand, and no beat
process was even running locally to fire the schedule at all.

This is the "single integration pass" §24's parallel-build note flagged
as needed afterward — touches `content/agents/copywriter.py`,
`content/views_scripts.py`, `content/views_posters.py`, `content/views.py`,
and `templates/admin/content-calendar.html`, the exact "do not touch"
file set §24 reserved for later.

### What changed

- **`copywriter.draft_now(item)`** — new wrapper around
  `draft_script_for_item`. Bypasses the LEAD-TIME gate (the admin is
  explicitly asking now) but deliberately keeps the INPUT-READINESS check
  (`_is_ready`) — forcing a draft from insufficient `context_notes` would
  just produce a bad script, not a useful override. On failure, files the
  same `INPUT_NEEDED` `ActionItem` the automated sweep would, so the
  bookkeeping is identical regardless of which path triggered it.
- **`ScriptsAPI.post`** gains `action=draft` (takes `plan_item_id`, not
  `script_id` — the one action that creates rather than reviews). Rejects
  if the item already has a script ("use Regenerate instead").
- **`PostersAPI.post`** gains `action=generate` (`plan_item_id`). No new
  designer.py code needed — `regenerate_poster(plan_item, ...)` already
  worked standalone for a FIRST poster (only requires an approved Script,
  never required a pre-existing `PosterAsset`); the gap was purely that
  the existing `PostersAPI` endpoint required an `id` to already exist.
  Rejects if a poster already exists, same reasoning as the script guard.
- **`ContentCalendarAPI.get`** now annotates each item with
  `has_script`/`has_approved_script`/`has_poster` (via
  `prefetch_related('scripts', 'posters')`, no extra queries) — drives
  which action the Calendar page offers next.
- **`content-calendar.html`**: once a plan is locked (approved), item rows
  used to show a dead "Locked" badge. Now shows the actual next pipeline
  step — "Draft Script" (no script yet) → "Awaiting script review" (links
  to Scripts page) → "Generate Poster" (approved script, poster content
  type, no poster yet) → "Poster generated" (links to Posters page) →
  "Script approved" (non-poster types, nothing further to trigger here).
  A skipped item just shows "Skipped". This is the direct, discoverable
  answer to the question that started this section.

### Verified live

Built a locked (`APPROVED`) test plan with one real `poster_learn` item
and a genuine context_notes brief (teaching the Kannada word "ಸಿಹಿ"). Full
chain, each step a real network call:
1. `has_script`/`has_approved_script`/`has_poster` all `false` initially.
2. **Draft Script** → real `learn-with-varthaai-script` skill run, produced
   a correct, WebSearch-verified Kannada/Malayalam translation pair.
   `has_script` flipped to `true`.
3. Approved the script via `ScriptsAPI` → `has_approved_script` flipped to
   `true`.
4. **Generate Poster** → real Gemini call, produced a 3.3MB image; the
   brief text explicitly confirmed it used the **approved copy verbatim**
   (the Script→Designer contract from §24 working correctly, not just
   present in the code). `has_poster` flipped to `true`.
5. Guard rails: drafting on an item with blank `context_notes` correctly
   failed with the readiness message and filed a real `INPUT_NEEDED`
   `ActionItem`; drafting on an already-scripted item and generating on an
   already-postered item were both correctly rejected ("use Regenerate
   instead").

All test plans/items deleted afterward — a first cleanup attempt that
tried to bundle verification and deletion in one command was correctly
blocked by the environment's safety classifier for touching data broadly;
redone as a separate, explicitly-scoped deletion of only the two test
plan IDs. `manage.py check` clean.
