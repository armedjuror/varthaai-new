"""
Copywriter Agent — content-generator-plan.md §3 ("why the Copywriter Agent
is an orchestrator, not a writer") and §13 Phase 2.

Unlike the Planner/Designer (pure text-in/JSON-out, `disallowed_tools=NO_TOOLS`),
this agent runs the actual reel-series Claude Skills verbatim inside a
`claude -p` session with **WebSearch/WebFetch enabled** — Learn/Inside/
Varthaanm treat live verification as a correctness requirement (content-
generator-plan.md §1/§3/§9). It still goes through core.claude_cli.run_claude_cli
rather than a direct `anthropic` call, same "no service authenticates with
ANTHROPIC_API_KEY" rule as every other agent in this codebase. Write/Edit/Bash
stay denied — the CLI's job is to produce text; this module (not the CLI) is
what writes to the DB.

Most days this agent has nothing to draft (§3): its real job is checking
whether each due PlanItem's required input actually exists yet, and only
generating a Script when it does.

--------------------------------------------------------------------------
STATUS-TRANSITION SCHEME (read this before writing Phase 3's Designer gate)
--------------------------------------------------------------------------
PlanItem.status:
  APPROVED (pre-script)   — admin approved this item as an idea; no Script
                             exists yet. due_plan_items() reads exactly this
                             state (see the gate note below) as "ready to draft".
  NEEDS_INPUT             — an input-readiness check failed; an open
                             ActionItem(INPUT_NEEDED) is filed (get_or_create,
                             so re-runs don't duplicate it). due_plan_items()
                             includes this status too (not just APPROVED) so
                             the item is re-checked on every subsequent run —
                             once context_notes is filled in (e.g. via
                             ContentCalendarAPI's update_item), the very next
                             run drafts it and closes the INPUT_NEEDED item
                             automatically. Self-healing, not a dead end.
  NEEDS_APPROVAL          — a Script (some version) has been drafted and is
                             awaiting the admin's read. Also the status a
                             regenerated item returns to (the admin hasn't
                             seen the new draft yet — same reasoning as
                             planner.regenerate_item resetting PlanItem back
                             to PLANNED for the same reason, §23).
  APPROVED (post-script)  — content/views_scripts.py's ScriptsAPI `approve`
                             action sets PlanItem.status back to APPROVED once
                             the admin approves the CURRENT (latest-version)
                             Script for it.

PlanItem.status == APPROVED is therefore OVERLOADED — it means either
"approved as an idea, no script yet" (pre-script) or "script approved, ready
for whatever comes next" (post-script), disambiguated only by whether an
APPROVED Script exists for the item. due_plan_items() disambiguates by
excluding items that already have an APPROVED Script
(`.exclude(scripts__status=Script.Status.APPROVED)`) — this is also the thing
that stops an item from being redrafted every day once its script is approved
(without it, ScriptsAPI's approve→PlanItem.status=APPROVED would re-match the
"ready to draft" gate on the very next run).

**Phase 3's Designer gate should read**: `PlanItem.status == APPROVED AND
PlanItem has an APPROVED Script AND PlanItem.content_type in the poster
content types` — the mirror image of due_plan_items' own filter.

**The due_plan_items() gate is `plan.status == APPROVED AND item.status ==
APPROVED`, not just the item's own status.** An item can be individually
toggled to APPROVED (content/views.py's `toggle_item_approve`) while its
parent plan is still NEEDS_REVIEW — that toggle is provisional until "Approve
Plan" locks the whole plan (§21); content-generator-plan.md §4's "only ever
read approved rows as input, never draft off an unreviewed plan" reads as the
WHOLE plan being reviewed, not one item's toggle state. Once a plan actually
is APPROVED, `approve_plan` bulk-converts every remaining PLANNED item to
APPROVED, so no PLANNED item survives inside an approved plan — item.status
is only ever APPROVED or SKIPPED at that point, which is why "status is
PLANNED or APPROVED" collapses to just APPROVED in practice.

Script.status: this module only ever creates Scripts directly as
NEEDS_REVIEW (Script.Status.DRAFT is unused by this agent). APPROVED /
CHANGES_REQUESTED are set exclusively by ScriptsAPI (content/views_scripts.py),
never written here.

Public entry points:
  due_plan_items(brand=None) -> QuerySet[PlanItem]
  draft_script_for_item(item) -> Script          # raises on failure
  regenerate_script(script_or_plan_item, instruction='') -> Script  # raises
  run_daily(brand=None) -> dict                  # never raises; per-item catch
"""
import logging
import shutil
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.models import ActionItem, ContentPlan, ContentSeries, PlanItem, Script

logger = logging.getLogger(__name__)

# WebSearch/WebFetch stay ON for this agent (unlike Planner/Designer) — derived
# from the shared NO_TOOLS list rather than hand-copying it, so this only ever
# diverges from the canonical "deny everything" list in exactly the two tools
# this agent actually needs (content-generator-plan.md §3/§9).
COPYWRITER_DISALLOWED_TOOLS = [t for t in NO_TOOLS if t not in ('WebSearch', 'WebFetch')]

_SKILLS_DIR = Path(__file__).resolve().parent.parent / 'skills'

# content_type -> (path under content/skills/, skill_used label stored on Script).
# reel_verdict is deliberately absent — never dispatched here (Phase 5).
_SKILL_MAP = {
    ContentSeries.ContentType.REEL_VARTHAANM: (
        'varthaai-varthaanm-script/SKILL.md', 'varthaai-varthaanm-script'),
    ContentSeries.ContentType.REEL_INSIDE: (
        'varthaai-inside-questions/SKILL.md', 'varthaai-inside-questions'),
    ContentSeries.ContentType.POSTER_LEARN: (
        'learn-with-varthaai-script/SKILL.md', 'learn-with-varthaai-script'),
    ContentSeries.ContentType.POSTER_OCCASION: (
        'varthaai-generic-content.md', 'varthaai-generic-content'),
    ContentSeries.ContentType.POSTER_EVENT: (
        'varthaai-generic-content.md', 'varthaai-generic-content'),
    ContentSeries.ContentType.BLOG: (
        'varthaai-generic-content.md', 'varthaai-generic-content'),
}

# content_types that go through the shared generic-content skill — the only
# ones whose output format (CONTENT_TYPE:/REGISTER: header) is regular enough
# to attempt a structured_json parse of (§13's "don't force a rigid parse the
# skills weren't designed to produce" — the three reel skills stay free text).
_GENERIC_CONTENT_TYPES = {
    ContentSeries.ContentType.POSTER_OCCASION,
    ContentSeries.ContentType.POSTER_EVENT,
    ContentSeries.ContentType.BLOG,
}

# Fallback prep-lead time for content_types whose ContentSeries row has no
# configured prep_lead_days (Blog/Occasion — see content/migrations/
# 0002_seed_series.py) or no series link at all (ad-hoc poster_event/occasion
# items the Planner creates with series_slug=null). Chosen so the Copywriter's
# draft is ready at least a day before the Designer's own poster-generation
# step, which runs 2 days before publish (content-generator-plan.md §13 Phase 3).
DEFAULT_PREP_LEAD_DAYS = 3

# Minimal readiness heuristic (content-generator-plan.md's own guidance: a
# false "not ready" on a genuinely usable Planner brief is worse than rarely
# missing a real placeholder) — just "is there something substantive here",
# not an elaborate placeholder-phrase detector.
_MIN_CONTEXT_NOTES_LEN = 15

_HEADLESS_NOTE = (
    "This is a fully automated, non-interactive run --- there is no one here "
    "to answer a follow-up question. Do NOT ask a clarifying question and "
    "stop; use ONLY the input given below and produce the complete final "
    "deliverable in one shot, in exactly the output format your instructions "
    "describe. If your instructions say to ask for something and it genuinely "
    "isn't provided below, make the most reasonable choice given the brand "
    "kit and the brief, and note that assumption in one short line at the end "
    "of your output instead of leaving the output incomplete or asking a "
    "question."
)

_RECAP_PREFIX = 'RECAP_FOR_NEXT_EPISODE:'


# --------------------------------------------------------------------------- #
# Skill loading                                                               #
# --------------------------------------------------------------------------- #
def _load_skill_text(relpath):
    """Loaded fresh off disk every call (no caching) — same "load fresh every
    time" principle as Brand.brand_kit (content-generator-plan.md §2)."""
    path = _SKILLS_DIR / relpath
    if not path.exists():
        raise RuntimeError(f'skill file not found: {path}')
    return path.read_text(encoding='utf-8')


def _strip_frontmatter(text):
    """Skill files open with a YAML frontmatter block (name/description,
    including an interactive "trigger on..." heuristic that's meaningless once
    we're directly injecting this as a `claude -p` system prompt rather than
    letting the CLI's own skill-discovery decide whether to load it) — strip
    it so the model only sees the actual creative instructions."""
    stripped = (text or '').strip()
    if stripped.startswith('---'):
        parts = stripped.split('---', 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return stripped


# --------------------------------------------------------------------------- #
# due_plan_items — see the module docstring for the exact gate reasoning.     #
# --------------------------------------------------------------------------- #
def due_plan_items(brand=None):
    """
    PlanItems whose series' prep_lead_days puts today at-or-past the prep
    deadline, that have been through the admin-approval gate (plan APPROVED
    *and* item APPROVED-or-NEEDS_INPUT — see module docstring), are not
    reel_verdict (Phase 5, hard-excluded same as the Planner), and don't
    already have an APPROVED Script (the thing that stops an approved-script
    item from being redrafted every subsequent day).

    NEEDS_INPUT is included alongside APPROVED so a previously-blocked item
    self-heals once an admin fills in context_notes (via
    ContentCalendarAPI's update_item, which deliberately doesn't touch
    status — see its own docstring) — without this, an item that once
    failed the readiness check would sit in NEEDS_INPUT forever, since
    nothing else ever moves it back to APPROVED. _is_ready() is re-checked
    on every run regardless of which of these two statuses got it here, so
    an item whose input is still missing just gets its (already-open,
    get_or_create-guarded) INPUT_NEEDED ActionItem left alone.
    """
    today = timezone.localdate()
    qs = (
        PlanItem.objects.filter(
            plan__status=ContentPlan.Status.APPROVED,
            status__in=(PlanItem.Status.APPROVED, PlanItem.Status.NEEDS_INPUT),
        )
        .exclude(content_type=ContentSeries.ContentType.REEL_VERDICT)
        .exclude(scripts__status=Script.Status.APPROVED)
        .select_related('series', 'plan', 'plan__brand')
        .distinct()
    )
    if brand is not None:
        qs = qs.filter(plan__brand=brand)

    due_ids = []
    for item in qs:
        lead_days = (
            item.series.prep_lead_days
            if item.series and item.series.prep_lead_days is not None
            else DEFAULT_PREP_LEAD_DAYS
        )
        if item.planned_date - timedelta(days=lead_days) <= today:
            due_ids.append(item.id)

    return (
        PlanItem.objects.filter(id__in=due_ids)
        .select_related('series', 'plan', 'plan__brand')
        .order_by('planned_date')
    )


# --------------------------------------------------------------------------- #
# Input readiness (§3 step 2 / §13's per-content_type check)                  #
# --------------------------------------------------------------------------- #
def _is_ready(item):
    """Returns (ready: bool, reason: str). poster_occasion/blog never block
    per varthaai-generic-content.md's own §1 table ("nothing else needed" /
    "angle is a nice-to-have, not a blocker") — only the three reel types
    (whose skills each open expecting a concrete brief) and poster_event
    (which must carry concrete date/time/venue/CTA specifics, "never
    inferred") actually gate on context_notes."""
    content_type = item.content_type
    if content_type in (ContentSeries.ContentType.POSTER_OCCASION, ContentSeries.ContentType.BLOG):
        return True, ''

    notes = (item.context_notes or '').strip()
    if len(notes) < _MIN_CONTEXT_NOTES_LEN:
        return False, (
            f'context_notes is blank or too short to draft "{item.working_title}" from — '
            'needs a concrete brief before the Copywriter Agent can run the skill.'
        )

    if content_type == ContentSeries.ContentType.POSTER_EVENT and not any(c.isdigit() for c in notes):
        return False, (
            'poster_event needs concrete date/time specifics in context_notes '
            '(none found) — the generic-content skill explicitly forbids inventing '
            'these, so this can\'t be drafted yet.'
        )

    return True, ''


def _file_input_needed(item, reason):
    ActionItem.objects.get_or_create(
        plan_item=item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN,
        defaults={
            'title': f'Input needed: {item.working_title}',
            'description': reason,
            'due_date': item.planned_date,
        },
    )
    item.status = PlanItem.Status.NEEDS_INPUT
    item.save(update_fields=['status', 'updated_at'])


# --------------------------------------------------------------------------- #
# Varthaanm continuity (§6) — recap_summary read from the most recent         #
# *approved* Script for this series, per brand.                               #
# --------------------------------------------------------------------------- #
def _latest_varthaanm_recap(item):
    prior = (
        Script.objects.filter(
            plan_item__content_type=ContentSeries.ContentType.REEL_VARTHAANM,
            plan_item__plan__brand=item.plan.brand,
            status=Script.Status.APPROVED,
        )
        .exclude(plan_item=item)
        .order_by('-plan_item__planned_date')
        .first()
    )
    return prior.recap_summary if prior else ''


def _next_varthaanm_episode_number(item):
    """No explicit episode-number field anywhere in the schema — derived as
    (count of prior APPROVED Varthaanm scripts for this brand) + 1, so a real
    number reaches the skill instead of shipping "EPISODE ?" in the output."""
    return Script.objects.filter(
        plan_item__content_type=ContentSeries.ContentType.REEL_VARTHAANM,
        plan_item__plan__brand=item.plan.brand,
        status=Script.Status.APPROVED,
    ).count() + 1


# --------------------------------------------------------------------------- #
# Prompt building                                                             #
# --------------------------------------------------------------------------- #
def build_copywriter_prompt(item, recap=None, episode_number=None):
    """The user-turn prompt — brand kit + this PlanItem's brief + (Varthaanm
    only) recap continuity. The skill's own instructions are injected
    separately as the `system_prompt` (see draft_script_for_item), not
    concatenated in here."""
    brand = item.plan.brand
    lines = [
        _HEADLESS_NOTE,
        '',
        'Brand kit:',
        brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults)',
        '',
    ]

    content_type = item.content_type
    if content_type == ContentSeries.ContentType.REEL_VARTHAANM:
        lines += [
            f'Episode number: {episode_number}',
            f"This week's story/incident (the admin's brief — treat as given, don't invent a different one): {item.context_notes}",
            "Last episode's recap: " + (
                recap or '(no prior approved episode found — write this as the origin/'
                'pilot episode framing if the story supports it, otherwise open without '
                'a recap beat rather than inventing one)'
            ),
            'Cliffhanger-or-not: not specified by the admin — use your judgment from the '
            'story given and state which you chose.',
        ]
    elif content_type == ContentSeries.ContentType.REEL_INSIDE:
        lines += [
            f'Episode topic (the admin\'s brief): {item.context_notes}',
        ]
    elif content_type == ContentSeries.ContentType.POSTER_LEARN:
        lines += [
            f'Situation/phrase brief (the admin\'s brief): {item.context_notes}',
        ]
    else:
        lines += [
            f'Working title: {item.working_title}',
            'Context/brief: ' + (item.context_notes or '(no additional context given — write from the working title alone)'),
        ]

    if content_type == ContentSeries.ContentType.REEL_VARTHAANM:
        lines += [
            '',
            'End your response with one final line in EXACTLY this format (used to carry '
            'continuity into next episode\'s recap beat, so it must be machine-parseable):',
            f'{_RECAP_PREFIX} <one sentence summarizing this episode\'s story and takeaway>',
        ]

    return '\n'.join(lines)


def _extract_recap_line(text):
    for line in (text or '').splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(_RECAP_PREFIX):
            return stripped.split(':', 1)[1].strip()
    return ''


def _parse_generic_structured(text):
    """Best-effort parse of varthaai-generic-content.md's own header format
    (`CONTENT_TYPE: ...` / `REGISTER: ...` then the copy) — only attempted for
    the three generic content_types; returns {} if the header isn't there
    rather than forcing a parse the skill wasn't designed to guarantee."""
    lines = (text or '').splitlines()
    data = {}
    body_start = 0
    for i, line in enumerate(lines[:6]):
        stripped = line.strip()
        if stripped.upper().startswith('CONTENT_TYPE:'):
            data['content_type'] = stripped.split(':', 1)[1].strip()
            body_start = i + 1
        elif stripped.upper().startswith('REGISTER:'):
            data['register'] = stripped.split(':', 1)[1].strip()
            body_start = i + 1
    if not data:
        return {}
    data['body'] = '\n'.join(lines[body_start:]).strip()
    return data


# --------------------------------------------------------------------------- #
# Core drafting call — shared by draft_script_for_item and regenerate_script. #
# --------------------------------------------------------------------------- #
def _run_skill_and_save(item, prompt_suffix=''):
    """Runs the mapped skill for `item` via `claude -p` (WebSearch/WebFetch
    ON) and persists the result as a new Script version + PlanItem status
    transition + ActionItem — see module docstring for the transition scheme.

    Raises on failure (missing skill mapping, missing CLI, CLI error, empty
    response) — same raise-on-failure contract as planner.generate_plan; the
    caller (run_daily, or the ScriptsAPI regenerate action) is responsible for
    catching it.

    The `claude -p` call happens BEFORE the DB transaction opens, so a CLI
    failure leaves nothing half-written — the item simply stays eligible for
    the next run/retry.
    """
    if item.content_type not in _SKILL_MAP:
        raise RuntimeError(f'No skill mapped for content_type={item.content_type!r}')
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    skill_relpath, skill_used = _SKILL_MAP[item.content_type]
    skill_text = _strip_frontmatter(_load_skill_text(skill_relpath))

    recap = episode_number = None
    if item.content_type == ContentSeries.ContentType.REEL_VARTHAANM:
        recap = _latest_varthaanm_recap(item)
        episode_number = _next_varthaanm_episode_number(item)

    prompt = build_copywriter_prompt(item, recap=recap, episode_number=episode_number)
    if prompt_suffix:
        prompt = f'{prompt}\n\n{prompt_suffix}'

    result = run_claude_cli(
        prompt,
        system_prompt=skill_text,
        disallowed_tools=COPYWRITER_DISALLOWED_TOOLS,
        model=settings.CONTENT_TEXT_MODEL,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'copywriter generation failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = (result.get('text') or '').strip()
    if not text:
        raise RuntimeError('copywriter generation returned no text')

    recap_summary = (
        _extract_recap_line(text) if item.content_type == ContentSeries.ContentType.REEL_VARTHAANM else ''
    )
    structured = _parse_generic_structured(text) if item.content_type in _GENERIC_CONTENT_TYPES else {}

    with transaction.atomic():
        next_version = (item.scripts.aggregate(Max('version'))['version__max'] or 0) + 1
        script = Script.objects.create(
            plan_item=item,
            skill_used=skill_used,
            raw_output=text,
            structured_json=structured,
            recap_summary=recap_summary,
            version=next_version,
            status=Script.Status.NEEDS_REVIEW,
        )
        item.status = PlanItem.Status.NEEDS_APPROVAL
        item.save(update_fields=['status', 'updated_at'])
        ActionItem.objects.get_or_create(
            plan_item=item, kind=ActionItem.Kind.SCRIPT_REVIEW, status=ActionItem.Status.OPEN,
            defaults={
                'title': f'Review script: {item.working_title}',
                'description': f'The Copywriter Agent drafted v{next_version} via {skill_used}.',
                'due_date': item.planned_date,
            },
        )
        # Self-heal (see due_plan_items' docstring): a successful draft means
        # whatever previously blocked this item (if it ever was NEEDS_INPUT)
        # no longer applies — close out the now-moot INPUT_NEEDED item rather
        # than leaving it open forever alongside a fresh SCRIPT_REVIEW one.
        ActionItem.objects.filter(
            plan_item=item, kind=ActionItem.Kind.INPUT_NEEDED, status=ActionItem.Status.OPEN,
        ).update(status=ActionItem.Status.DONE, resolved_at=timezone.now())
    return script


def draft_script_for_item(item):
    """Draft a first (or next) Script version for a PlanItem already past the
    input-readiness check. See _run_skill_and_save for the failure contract."""
    return _run_skill_and_save(item)


def regenerate_script(script_or_plan_item, instruction=''):
    """
    Re-runs the same skill for one PlanItem, optionally steered by a free-text
    instruction — mirrors planner.regenerate_item's shape (content-generator-
    plan.md §23). Always creates a NEW Script version (Script rows are
    append-only per plan-item; an approved Script is never mutated), and
    resets PlanItem.status back to NEEDS_APPROVAL regardless of its current
    status — the admin hasn't seen this new draft yet, so whatever review
    state applied to the previous version no longer means anything.

    Accepts either a Script or a PlanItem for convenience (ScriptsAPI's
    `regenerate` action already has the Script row in hand; run_daily/tests
    may only have the PlanItem).
    """
    item = script_or_plan_item.plan_item if isinstance(script_or_plan_item, Script) else script_or_plan_item

    if instruction:
        suffix = (
            'This is a REGENERATION of an existing draft — the admin reviewed the '
            f'previous version and asked for this specific change: {instruction}\n'
            'Produce a genuinely revised draft honoring that instruction, not a copy '
            'of what a previous version might have said.'
        )
    else:
        suffix = (
            'This is a REGENERATION of an existing draft — no specific instruction was '
            'given. Produce a genuinely different, improved take on the same brief, not '
            'a trivial reword.'
        )

    return _run_skill_and_save(item, prompt_suffix=suffix)


# --------------------------------------------------------------------------- #
# Daily orchestration entry point (§3) — importable/call-compatible with how  #
# content/tasks.py's run_planner_task calls planner.generate_plan(plan): a    #
# future @shared_task wrapper calls run_daily(), catches                     #
# SoftTimeLimitExceeded/Exception around the WHOLE call for infra-level       #
# failures, while this function itself never raises for a single item's      #
# failure (that's caught and counted here so one bad item doesn't stop the   #
# rest of the day's run).                                                    #
# --------------------------------------------------------------------------- #
def run_daily(brand=None):
    drafted = needs_input = failed = skipped = 0

    for item in due_plan_items(brand=brand):
        if item.content_type not in _SKILL_MAP:
            logger.warning(
                'copywriter: no skill mapped for content_type=%r (plan_item=%s) — skipping',
                item.content_type, item.id)
            skipped += 1
            continue

        ready, reason = _is_ready(item)
        if not ready:
            _file_input_needed(item, reason)
            needs_input += 1
            continue

        try:
            draft_script_for_item(item)
            drafted += 1
        except Exception:
            logger.exception('copywriter: failed to draft script for plan_item %s', item.id)
            failed += 1

    logger.info(
        'copywriter.run_daily: drafted=%s needs_input=%s failed=%s skipped=%s',
        drafted, needs_input, failed, skipped)
    return {'drafted': drafted, 'needs_input': needs_input, 'failed': failed, 'skipped': skipped}
