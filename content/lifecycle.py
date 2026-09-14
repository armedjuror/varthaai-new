"""
Derived 5-stage content lifecycle: Planned -> Scripted -> Generated ->
Approved -> Published. Computed from existing Script/PosterAsset signals
wherever one already exists — PlanItem.status itself keeps its original
job (the item-wise approve/skip gate, see content/views.py's
ContentCalendarAPI docstring) and is not repurposed into a 5-value enum.

A stage only exists once an item has passed that gate ("each approved plan
item is a task" — the progression describes work on a task, not the
pre-approval idea stage) and isn't SKIPPED.

Reels have no in-system artifact the Approved stage can key off: a reel's
Script is reviewed/approved independently (the written caption/copy), same
as any other content type, but that says nothing about whether the reel
has actually been filmed/edited/approved for posting. Naively reusing
has_approved_script for a reel's Approved stage collides with Generated —
scripts are normally approved BEFORE filming, so has_approved_script would
go true first and skip straight past 'generated'. So both Generated and
Approved are manual admin milestones for reels
(manually_generated_at/manually_approved_at), set via the
mark_generated/mark_approved actions.
"""
from datetime import timedelta

from content.models import ContentSeries, PlanItem

REEL_CONTENT_TYPES = (
    ContentSeries.ContentType.REEL_VARTHAANM,
    ContentSeries.ContentType.REEL_VERDICT,
    ContentSeries.ContentType.REEL_INSIDE,
)
POSTER_CONTENT_TYPES = (
    ContentSeries.ContentType.POSTER_LEARN,
    ContentSeries.ContentType.POSTER_OCCASION,
    ContentSeries.ContentType.POSTER_EVENT,
)

STAGE_LABELS = {
    'planned': 'Planned',
    'scripted': 'Scripted',
    'generated': 'Generated',
    'approved': 'Approved',
    'published': 'Published',
}


def lifecycle_stage(item, has_script, has_approved_script, has_poster, has_approved_poster):
    """None if the item isn't a "task" yet (still undecided PLANNED, or
    SKIPPED) — otherwise one of STAGE_LABELS' keys."""
    if item.status == PlanItem.Status.SKIPPED:
        return None
    if item.status == PlanItem.Status.POSTED:
        return 'published'
    if item.status == PlanItem.Status.PLANNED:
        return None

    if item.content_type in REEL_CONTENT_TYPES:
        if item.manually_approved_at:
            return 'approved'
        if item.manually_generated_at:
            return 'generated'
        if has_script:
            return 'scripted'
        return 'planned'

    if item.content_type in POSTER_CONTENT_TYPES:
        if has_approved_script and has_approved_poster:
            return 'approved'
        if has_poster:
            return 'generated'
        if has_script:
            return 'scripted'
        return 'planned'

    # Blog — scripted itself IS generated, no separate image/poster step.
    if has_approved_script:
        return 'approved'
    if has_script:
        return 'scripted'
    return 'planned'


def prep_due_date(item):
    """The date by which this item's series expects prep (drafting) to
    begin — informational only. NOT a deadline by which 'Approved' must be
    reached: copywriter.due_plan_items() uses this exact date as when
    drafting *starts*, so review/approval time only begins on this date —
    an "overdue if not Approved by here" flag would be wrong by
    construction, lighting up the moment an item becomes draftable."""
    from content.agents.copywriter import DEFAULT_PREP_LEAD_DAYS
    lead_days = (
        item.series.prep_lead_days
        if item.series and item.series.prep_lead_days is not None
        else DEFAULT_PREP_LEAD_DAYS
    )
    return item.planned_date - timedelta(days=lead_days)
