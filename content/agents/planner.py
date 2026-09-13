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

Public entry point:
  generate_plan(plan) -> int   # number of PlanItems created
"""
import json
import logging
import shutil

from django.conf import settings
from django.utils.dateparse import parse_date

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.agents._util import strip_code_fence
from content.models import ContentSeries, PlanItem, TrendFlag

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


def build_planner_prompt(plan):
    brand = plan.brand
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

Task: propose the content plan for the recurring series above across the planning period, honoring each series' weekday/cadence exactly (skip a slot only if it falls outside the period). Also propose occasion posters for any real festivals, national/regional (Kerala/Karnataka) observances, or notable days that fall within the period and suit a food brand — do not invent a holiday that doesn't exist. Do NOT propose anything for the Varthaai Verdict series even though it's an active series — that series is intentionally excluded from planning for now (it will be turned on in a later phase).

Respond with ONLY a JSON array, no other text, of objects with exactly these keys:
{{"planned_date": "YYYY-MM-DD", "series_slug": "<slug from the list above, or null for occasion/event posters not tied to a series>", "content_type": "<one of: reel_varthaanm, reel_inside, poster_learn, poster_occasion, poster_event, blog>", "working_title": "short internal working title, not the final caption", "context_notes": "concrete context for whoever writes this next — the occasion/angle/detail, not vague filler"}}

Rules:
- Every planned_date must fall within the planning period given above.
- Never propose content_type "reel_verdict".
- context_notes must contain a real, specific detail — never "TBD" or empty."""


def generate_plan(plan):
    """
    Calls Claude once, parses the JSON array, and creates PlanItems for any
    (planned_date, content_type) combination not already present in this
    plan — existing rows (from a prior run, or a manual admin edit) are
    never touched or duplicated, so re-running is additive only, never
    destructive (an admin-reviewed plan is only ever improvised on, never
    silently overwritten — content-generator-plan.md §1).

    Returns the number of PlanItems created. Raises on failure (missing CLI,
    error result, parse failure) — the caller (content/tasks.py) is
    responsible for catching it and recording plan.generation_error.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    prompt = build_planner_prompt(plan)
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
        if content_type == ContentSeries.ContentType.REEL_VERDICT:
            continue  # Verdict is deliberately excluded from planning for now.
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
        PlanItem.objects.create(
            plan=plan,
            series=series,
            content_type=content_type,
            planned_date=planned_date,
            working_title=working_title[:255],
            context_notes=item.get('context_notes') or '',
        )
        created += 1

    return created
