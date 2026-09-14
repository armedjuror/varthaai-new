"""
Planner Agent — Varthaai's content calendar generator (content-generator-
plan.md §8, "on-demand trigger + bootstrapping").

Non-agentic: a single headless `claude -p` turn (core.claude_cli.run_claude_cli)
with an empty tool allowlist — text in, structured JSON out. The Planner
doesn't need filesystem/DB/web tool access, since all the context it needs
(brand kit, series cadence, existing plan items, flagged trends) is fetched
by our own code and handed to it in the prompt; it still goes through
run_claude_cli rather than a direct Anthropic API call, because no service in
this codebase authenticates with ANTHROPIC_API_KEY — see core/claude_cli.py.

Public entry points:
  generate_plan(plan, extra_instruction='') -> int  # number of PlanItems
                                       # created; extra_instruction optionally
                                       # steers the proposals (regenerate_plan's
                                       # admin instruction passes through here)
  regenerate_plan(plan, instruction='') -> dict  # deletes untouched items
                                       # (PLANNED, no Script/PosterAsset) and
                                       # re-runs generate_plan for the same
                                       # period — approved/skipped/drafted/
                                       # generated items are never touched
  regenerate_item(item, instruction='') -> PlanItem  # fresh working_title/
                                       # context_notes for one existing item
  propose_change_for_item(item, trend_texts) -> dict | None  # one item's
                                       # proposed diff, or None if no change
  run_daily_nudge(brand=None) -> dict # never raises; per-item catch
                                       # (content-generator-plan.md §13
                                       # Phase 1 step 3 / Phase 4's
                                       # review-diff UI)
"""
import json
import logging
import shutil

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.agents._util import strip_code_fence
from content.models import ActionItem, ContentSeries, PlanItem, TrendFlag

logger = logging.getLogger(__name__)

_WEEKDAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def _series_context():
    lines = []
    for s in ContentSeries.objects.filter(is_active=True).order_by('name'):
        if s.weekday is not None:
            cadence = f'every {"other " if s.is_alternate_week else ""}{_WEEKDAY_NAMES[s.weekday]}'
        else:
            cadence = 'flexible/ad-hoc timing'
        lead = f', prepared {s.prep_lead_days} day(s) before' if s.prep_lead_days else ''
        lines.append(
            f'- {s.name} (slug: {s.slug}, content_type: {s.content_type}, '
            f'format: {s.format}): {cadence}{lead}')
    return '\n'.join(lines) or '(no active series configured)'


def _existing_items_context(plan):
    items = plan.items.select_related('series').order_by('planned_date')
    if not items:
        return '(none yet — this plan is empty)'
    return '\n'.join(
        f'- {it.planned_date}: [{it.series.name if it.series else it.content_type}] '
        f'{it.working_title} (status: {it.status})'
        for it in items)


def _recent_history_context(brand, before_date, lookback_days=90):
    from datetime import timedelta
    cutoff = before_date - timedelta(days=lookback_days)
    items = (
        PlanItem.objects.filter(
            plan__brand=brand, planned_date__gte=cutoff, planned_date__lt=before_date,
        )
        .exclude(status=PlanItem.Status.SKIPPED)
        .select_related('series')
        .order_by('-planned_date')[:40]
    )
    if not items:
        return '(no history yet)'
    return '\n'.join(
        f'- {it.planned_date}: [{it.series.name if it.series else it.content_type}] {it.working_title}'
        for it in items)


def _trend_context():
    flags = TrendFlag.objects.filter(
        considered=False, plan_item__isnull=True,
    ).order_by('-flagged_at')[:20]
    if not flags:
        return '(none flagged)'
    return '\n'.join(f'- {f.source_text}' for f in flags)


# Shared between build_planner_prompt (proposing new items) and
# build_regenerate_item_prompt (reworking one existing item) — both need
# the identical bar for what a usable context_notes actually is, so it's
# one copy, not two that can quietly drift apart.
_CONTEXT_NOTES_GUIDANCE = """context_notes is what actually gets handed to whoever writes the script/poster/blog next — treat it as a real creative brief, not a description of the series format (the writer already knows the format from content_type/series). It must commit to ONE specific, concrete idea they can start writing from immediately, with no further invention needed on their part. Never write it as a description of what this TYPE of content usually covers — that's a category, not a brief.

Banned in context_notes: hedge words/phrases ("e.g.", "such as", "consider", "maybe", "could", "a general", "an evergreen angle", "something like", "or" listing multiple options instead of picking one), and any sentence whose real content could be deleted and replaced with "[insert topic here]" without losing information.

What "specific" means per content_type:
- reel_varthaanm (Ajwad's personal story-telling reel): name ONE real story, moment, realization, or customer interaction to tell — not "share something personal." Bad: "Talk about the brand's journey." Good: "Tell the story of the first batch that got rejected by a distributor for not being crunchy enough, and what changed after."
- reel_inside (interview with Saad, alternate Mondays): write 2-3 SPECIFIC interview questions tied to one real theme (sourcing, quality control, a recent operational decision) — not "behind the scenes." Bad: "Ask about daily operations." Good: "Ask Saad: How do we test a new banana supplier before committing? What's the biggest mistake we made in year one of sourcing?"
- poster_learn (teaching a word/phrase, currently Kannada): name the EXACT word or phrase being taught, its meaning, and how it's used — not "an educational angle." Bad: "Teach a food-related word." Good: "Teach the Kannada word 'ರುಚಿ' (ruchi) — meaning 'taste', used to compliment food, e.g. 'ತುಂಬಾ ರುಚಿ' (very tasty)."
- poster_occasion / poster_event: name the specific occasion/event AND the specific angle tying it to the brand — commit even if the exact date is approximate, don't hedge with "if X doesn't fall here, do Y instead."
- blog: state one specific thesis/claim/story the post argues or tells — not a generic topic category. Bad: "Write about seasonal eating." Good: "Write about why banana chips are a smarter monsoon snack than fried alternatives — shelf life, oil absorption, and how Varthaai's packaging keeps them crisp in humidity."
- reel_verdict (blind competitor snack review — Ajwad buys/tastes/photographs a real competitor product): you do NOT know which product this will be — that's Ajwad's own real-world purchase decision, supplied later through a dedicated intake form (product photo, ingredient/nutrition labels, Design/Pricing/Taste scores), never through context_notes. Reserve the slot with a placeholder: working_title exactly "Varthaai Verdict — <date>" and context_notes exactly "Awaiting product selection and intake from Ajwad (photos, scores) — do not draft until submitted." NEVER invent a competitor product, brand name, or score for this content_type."""


def build_planner_prompt(plan, extra_instruction=''):
    brand = plan.brand
    instruction_block = (
        f"\nAdditional instruction from the admin for this planning pass — follow it (it can ask "
        f"for a specific addition, or to skip/leave out a specific slot; it CANNOT alter or remove "
        f"anything listed above under \"Items already in THIS plan\" — those are fixed): "
        f"{extra_instruction}\n"
        if extra_instruction else ''
    )
    return f"""Brand kit:
{brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults)'}

Planning period: {plan.period_start} to {plan.period_end}

Recurring series and their cadence:
{_series_context()}

Items already in THIS plan (do not duplicate these dates/content types — only propose items for slots not already covered):
{_existing_items_context(plan)}

Recent post history (last ~90 days, for continuity/variety — don't repeat the same angle or occasion):
{_recent_history_context(brand, plan.period_start)}

Manually flagged trends/topics to consider weaving in (may be empty):
{_trend_context()}
{instruction_block}
Task: propose the content plan for the recurring series above across the planning period, honoring each series' weekday/cadence exactly (skip a slot only if it falls outside the period) — this INCLUDES reel_verdict (Varthaai Verdict), reserved as a placeholder slot per the reel_verdict rule below, never left off the calendar. Also propose occasion posters for any real festivals, national/regional (Kerala/Karnataka) observances, or notable days that fall within the period and suit a food brand — do not invent a holiday that doesn't exist.

Respond with ONLY a JSON array, no other text, of objects with exactly these keys:
{{"planned_date": "YYYY-MM-DD", "series_slug": "<slug from the list above, or null for occasion/event posters not tied to a series>", "content_type": "<one of: reel_varthaanm, reel_inside, reel_verdict, poster_learn, poster_occasion, poster_event, blog>", "working_title": "short internal working title, not the final caption", "context_notes": "a concrete, ready-to-write brief — see rules below"}}

{_CONTEXT_NOTES_GUIDANCE}

Rules:
- Every planned_date must fall within the planning period given above.
- For reel_verdict specifically: working_title/context_notes must be EXACTLY the placeholder text given in the reel_verdict rule above — no exceptions, no invented product."""


def build_regenerate_item_prompt(item, instruction=''):
    plan = item.plan
    brand = plan.brand
    series = item.series
    series_line = (
        f'{series.name} (content_type: {series.content_type}, format: {series.format})'
        if series else f'content_type: {item.content_type} (no series link)'
    )
    steer = (
        f'Admin\'s specific instruction for this regeneration — follow it: {instruction}'
        if instruction else
        'No specific instruction given — produce a genuinely different, better idea than '
        'the current one, not a trivial reword of it.'
    )
    return f"""Brand kit:
{brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults)'}

You are regenerating ONE existing plan item, not the whole plan. Its date, series, and content type are fixed — only propose a new working_title and context_notes for this same slot.

Series/content type: {series_line}
Scheduled date: {item.planned_date}

Current working_title: {item.working_title}
Current context_notes: {item.context_notes or '(empty)'}

Recent post history (last ~90 days, for continuity/variety — don't repeat the same angle or occasion):
{_recent_history_context(brand, plan.period_start)}

{steer}

Respond with ONLY a JSON object, no other text, with exactly these keys:
{{"working_title": "short internal working title, not the final caption", "context_notes": "a concrete, ready-to-write brief — see rules below"}}

{_CONTEXT_NOTES_GUIDANCE}"""


def generate_plan(plan, extra_instruction=''):
    """
    Calls Claude once, parses the JSON array, and creates PlanItems for any
    (planned_date, content_type) combination not already present in this
    plan — existing rows (from a prior run, or a manual admin edit) are
    never touched or duplicated, so re-running is additive only, never
    destructive (an admin-reviewed plan is only ever improvised on, never
    silently overwritten — content-generator-plan.md §1).

    `extra_instruction` optionally steers the proposals for open slots
    (regenerate_plan passes the admin's free-text instruction through here)
    — it has no effect on which existing rows get skipped, that dedup is
    still purely key-based below.

    Returns the number of PlanItems created. Raises on failure (missing CLI,
    error result, parse failure) — the caller (content/tasks.py) is
    responsible for catching it and recording plan.generation_error.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    prompt = build_planner_prompt(plan, extra_instruction=extra_instruction)
    result = run_claude_cli(
        prompt,
        disallowed_tools=NO_TOOLS,
        model=settings.CONTENT_TEXT_MODEL,
        max_turns=1,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'plan generation failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = result.get('text') or ''

    try:
        proposed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'plan generation returned unparseable JSON: {exc}') from exc
    if not isinstance(proposed, list):
        raise RuntimeError('plan generation returned JSON that is not a list')

    series_by_slug = {s.slug: s for s in ContentSeries.objects.all()}
    existing_keys = set(plan.items.values_list('planned_date', 'content_type'))

    created = 0
    for item in proposed:
        if not isinstance(item, dict):
            continue
        content_type = item.get('content_type')
        working_title = (item.get('working_title') or '').strip()
        planned_date = parse_date(item.get('planned_date') or '')
        if not (planned_date and content_type and working_title):
            logger.warning('planner: skipping malformed proposed item: %r', item)
            continue
        if content_type not in ContentSeries.ContentType.values:
            logger.warning('planner: skipping unknown content_type: %r', item)
            continue
        if not (plan.period_start <= planned_date <= plan.period_end):
            continue
        key = (planned_date, content_type)
        if key in existing_keys:
            continue
        existing_keys.add(key)

        series = series_by_slug.get(item.get('series_slug') or '')
        context_notes = item.get('context_notes') or ''
        if content_type == ContentSeries.ContentType.REEL_VERDICT:
            # Defense in depth for the "never invent a competitor product"
            # rule (build_planner_prompt's reel_verdict instructions) — force
            # the exact placeholder regardless of what the model actually
            # wrote, rather than trusting it followed instructions.
            working_title = f'Varthaai Verdict — {planned_date.isoformat()}'
            context_notes = (
                'Awaiting product selection and intake from Ajwad '
                '(photos, scores) — do not draft until submitted.'
            )
        PlanItem.objects.create(
            plan=plan,
            series=series,
            content_type=content_type,
            planned_date=planned_date,
            working_title=working_title[:255],
            context_notes=context_notes,
        )
        created += 1

    return created


def regenerate_item(item, instruction=''):
    """
    Rewrites ONE existing PlanItem's working_title/context_notes in place —
    same slot (plan/planned_date/content_type/series untouched), a fresh
    creative take. `instruction` optionally steers it (e.g. "make it about
    pricing instead"); without one, the prompt just asks for something
    genuinely different from the current version, not a trivial reword.

    Deliberately resets item.status back to PLANNED regardless of what it
    was before — unlike a manual edit (content-generator-plan.md §21's
    update_item, which leaves status alone because the admin typed the new
    text themselves and implicitly re-approved it), a regeneration produces
    content the admin hasn't seen yet, so any prior approve/skip decision
    no longer means anything and must be made again.

    Raises on failure (missing CLI, error result, parse failure) — same
    contract as generate_plan; the caller is responsible for turning that
    into an API error response.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    prompt = build_regenerate_item_prompt(item, instruction)
    result = run_claude_cli(
        prompt,
        disallowed_tools=NO_TOOLS,
        model=settings.CONTENT_TEXT_MODEL,
        max_turns=1,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'item regeneration failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = result.get('text') or ''

    try:
        proposed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'item regeneration returned unparseable JSON: {exc}') from exc
    if not isinstance(proposed, dict):
        raise RuntimeError('item regeneration returned JSON that is not an object')

    working_title = (proposed.get('working_title') or '').strip()
    if not working_title:
        raise RuntimeError('item regeneration returned an empty working_title')

    item.working_title = working_title[:255]
    item.context_notes = proposed.get('context_notes') or ''
    item.status = PlanItem.Status.PLANNED
    item.save(update_fields=['working_title', 'context_notes', 'status', 'updated_at'])
    return item


def regenerate_plan(plan, instruction=''):
    """
    Plan-level "Regenerate" — reworks a whole ContentPlan in place instead
    of one item at a time. Deletes every item that hasn't been touched yet
    (still PLANNED — the admin never approved or skipped it — with no
    Script and no PosterAsset, i.e. genuinely just an idea, nothing
    produced or decided), then re-runs generate_plan for the same period so
    the freed slots get fresh proposals and any date/content_type combo
    still missing from the period gets filled in — exactly like the
    original generation pass.

    NEVER touches an item that's been approved, skipped, drafted, or
    generated in any way (explicit product requirement: "NO approved item
    should be touched when a plan is regenerated") — those are excluded
    from the delete and therefore also from build_planner_prompt's
    proposal (they still show up in _existing_items_context, so the
    Planner won't duplicate their slot). Note this means an item that's
    been manually EDITED but never approved is NOT protected — editing and
    approving are different actions (content/views.py's _update_item), and
    only approving marks an item as decided.

    `instruction` is optional free-text steering for the fresh proposals on
    the freed/open slots only — see build_planner_prompt.

    Returns {'deleted': int, 'created': int}. Raises the same way
    generate_plan does — same caller contract.
    """
    eligible = (
        plan.items
        .annotate(script_count=Count('scripts', distinct=True), poster_count=Count('posters', distinct=True))
        .filter(status=PlanItem.Status.PLANNED, script_count=0, poster_count=0)
    )
    deleted = eligible.count()
    eligible.delete()
    created = generate_plan(plan, extra_instruction=instruction)
    return {'deleted': deleted, 'created': created}


# --------------------------------------------------------------------------- #
# Phase 4 — proposing changes to an ALREADY-APPROVED plan (content-generator-
# plan.md §4: "an already-approved plan can still be improvised on later
# Planner runs, but a proposed change sits as a diff awaiting separate
# approval — it never silently overwrites an approved plan"). This is the
# "daily nudge" described in §13 Phase 1 step 3, built here rather than in
# Phase 1 because there was nothing to review it with until now.
# --------------------------------------------------------------------------- #
def _open_trend_flags():
    """Same pool _trend_context() draws from when generating a brand-new
    plan (considered=False, not yet linked to any item) — reused here so a
    flagged trend is available to EITHER path, whichever runs first."""
    return TrendFlag.objects.filter(considered=False, plan_item__isnull=True).order_by('-flagged_at')[:20]


def build_propose_change_prompt(item, trend_texts):
    series = item.series
    series_line = (
        f'{series.name} (content_type: {series.content_type}, format: {series.format})'
        if series else f'content_type: {item.content_type} (no series link)'
    )
    trends_block = '\n'.join(f'- {t}' for t in trend_texts)
    return f"""Brand kit:
{item.plan.brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults)'}

This item is ALREADY APPROVED and scheduled. You are being asked whether the newly flagged trends below make a genuinely better angle worth proposing for THIS SAME slot — not to change it just for the sake of change. Most of the time the right answer is "no change" — only propose one if a trend meaningfully improves this specific item.

Series/content type: {series_line}
Scheduled date: {item.planned_date}

Current working_title: {item.working_title}
Current context_notes: {item.context_notes or '(empty)'}

Newly flagged trends/topics to consider:
{trends_block}

Respond with ONLY a JSON object, no other text, with exactly these keys:
{{"has_change": true or false, "working_title": "...", "context_notes": "..."}}

If has_change is false, still fill working_title/context_notes with the CURRENT values unchanged (so the response is always a complete object) — but has_change must reflect your genuine recommendation, never default it to true just to have something to propose.

{_CONTEXT_NOTES_GUIDANCE}"""


def propose_change_for_item(item, trend_texts):
    """
    Asks the Planner whether newly flagged trends warrant a different angle
    for ONE already-approved item. Returns {'working_title':...,
    'context_notes':...} if a genuine change is proposed, or None if the
    model recommends leaving it as-is. Raises on failure (missing CLI,
    error result, parse failure) — same contract as generate_plan/
    regenerate_item; the caller (run_daily_nudge) is responsible for
    catching it per-item so one bad item doesn't stop the rest of the run.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    prompt = build_propose_change_prompt(item, trend_texts)
    result = run_claude_cli(
        prompt,
        disallowed_tools=NO_TOOLS,
        model=settings.CONTENT_TEXT_MODEL,
        max_turns=1,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'change-proposal generation failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = result.get('text') or ''

    try:
        proposed = json.loads(strip_code_fence(text))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'change-proposal generation returned unparseable JSON: {exc}') from exc
    if not isinstance(proposed, dict):
        raise RuntimeError('change-proposal generation returned JSON that is not an object')

    if not proposed.get('has_change'):
        return None

    working_title = (proposed.get('working_title') or '').strip()
    if not working_title:
        raise RuntimeError('change-proposal generation returned an empty working_title')

    return {'working_title': working_title[:255], 'context_notes': proposed.get('context_notes') or ''}


def run_daily_nudge(brand=None):
    """
    Daily sweep (content-generator-plan.md §13 Phase 1 step 3): for every
    gate-approved, still-future PlanItem, ask whether newly flagged trends
    warrant a different angle — but ONLY if there are genuinely new
    (considered=False) TrendFlags to react to; most days, for most brands,
    this makes zero LLM calls.

    A proposal is written to PlanItem.proposed_changes (never applied
    directly — see this section's module docstring) and surfaced via an
    ITEM_CHANGE_PROPOSED ActionItem; content/views.py's ContentCalendarAPI
    (`accept_proposal`/`reject_proposal`) applies or discards it.

    Never touches: reel_verdict items (Phase 5), items whose planned_date
    has already passed (nothing left to "improvise" on), items that already
    have any Script (the Copywriter has moved past the planning stage —
    changing the brief under a script in progress would be confusing, not
    helpful), or items that already carry a pending proposal (don't stack a
    second one on top before the admin resolves the first).

    Gates on item.status == APPROVED alone, not plan.status — same
    item-wise-only reasoning as copywriter.due_plan_items() (§26/§27:
    nothing sets ContentPlan.status to APPROVED any more since plan-level
    approval was removed, so a plan__status=APPROVED filter here would
    never match anything in production).

    Trend flags are only marked considered=True once actually fed to at
    least one eligible item this run — a brand with open trends but zero
    eligible items (e.g. no approved plan yet) leaves them open for the
    next run that does have something to apply them to.

    Returns {'proposed': int, 'unchanged': int, 'failed': int} — never
    raises; each item's failure is caught and counted, same idiom as
    copywriter.run_daily/designer.run_daily.
    """
    trend_flags = list(_open_trend_flags())
    if not trend_flags:
        return {'proposed': 0, 'unchanged': 0, 'failed': 0}
    trend_texts = [f.source_text for f in trend_flags]

    today = timezone.localdate()
    qs = (
        PlanItem.objects.filter(
            status=PlanItem.Status.APPROVED,
            planned_date__gt=today,
            proposed_changes={},
        )
        .exclude(content_type=ContentSeries.ContentType.REEL_VERDICT)
        .exclude(scripts__isnull=False)
        .select_related('plan', 'plan__brand', 'series')
        .distinct()
    )
    if brand is not None:
        qs = qs.filter(plan__brand=brand)
    items = list(qs)

    summary = {'proposed': 0, 'unchanged': 0, 'failed': 0}
    if not items:
        return summary

    for item in items:
        try:
            proposal = propose_change_for_item(item, trend_texts)
        except Exception:
            logger.exception('planner: propose_change_for_item failed for plan_item %s', item.id)
            summary['failed'] += 1
            continue
        if not proposal:
            summary['unchanged'] += 1
            continue
        item.proposed_changes = proposal
        item.save(update_fields=['proposed_changes', 'updated_at'])
        ActionItem.objects.get_or_create(
            plan_item=item, kind=ActionItem.Kind.ITEM_CHANGE_PROPOSED, status=ActionItem.Status.OPEN,
            defaults={
                'title': f'Proposed change: {item.working_title}',
                'description': 'The Planner proposed a different angle based on newly flagged trends.',
                'due_date': item.planned_date,
            },
        )
        summary['proposed'] += 1

    TrendFlag.objects.filter(id__in=[f.id for f in trend_flags]).update(considered=True)
    return summary
