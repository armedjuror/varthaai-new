"""
Designer Agent — Varthaai's poster generation pipeline.

Implements poster-generation-plan.md, adapted for this codebase in two ways:
  - Local Django media storage instead of S3 — this app doesn't use S3
    anywhere (CLAUDE.md: "File uploads | Django media files"); ImageField
    is consistent with how `Brand.logo` already works.
  - Brief generation is a headless `claude -p` turn (core.claude_cli.
    run_claude_cli) with an empty tool allowlist — text in, text out, no
    tools — same non-agentic pattern as debugger's `_call_advisor` (see
    debugger/agent.py), but NOT a direct `anthropic` API call: no service in
    this codebase authenticates with ANTHROPIC_API_KEY (see
    core/claude_cli.py's docstring for why).
  - Image generation is the one step Claude can't do at all — that call
    goes to Gemini 3 Pro Image ("Nano Banana Pro", GEMINI_API_KEY, a
    different provider, unrelated to the "no Anthropic API" rule above).
    The calling convention (client.models.generate_content with a
    GenerateContentConfig(response_modalities=['TEXT','IMAGE'],
    image_config=ImageConfig(...)), reading the image back off
    response.candidates[0].content.parts[].inline_data) has been verified
    against the current googleapis/python-genai SDK README and Google
    Cloud's Gemini 3 Pro Image docs (Sept 2026) — cross-checked across
    multiple sources after an initial doc fetch surfaced a DIFFERENT,
    inconsistent `client.interactions.create(...)` shape that didn't
    reconcile with the SDK's own README. Still genuinely UNTESTED end-to-
    end in this environment (no GEMINI_API_KEY configured here) — the
    first real run should be watched closely.

Public entry points:
  infer_topic_category(topic) -> str
  select_poster_inspiration(brand, topic, format) -> PosterInspiration | None
  list_eligible_inspirations(brand, topic=None, format=None) -> QuerySet
  select_brand_asset(brand, tag=None) -> BrandAsset | None
  generate_poster_brief(brand, topic, inspiration, context_notes='', approved_copy=None, has_brand_logo=False) -> str
  generate_poster_image(brief, inspiration, ...) -> tuple[bytes | None, dict]
  run_daily(brand=None) -> dict                       # Phase 3 daily sweep
  regenerate_poster(plan_item, instruction='', inspiration_id=None) -> PosterAsset
"""
import logging
import mimetypes
import shutil
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.models import (
    ActionItem, BrandAsset, ContentSeries, PlanItem, PosterAsset, PosterFormat, PosterInspiration, Script,
)

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except Exception:  # pragma: no cover - not installed in this environment
    GENAI_AVAILABLE = False


# --------------------------------------------------------------------------- #
# Topic categorization — keyword rules, not an LLM call (cheap, deterministic;
# mirrors zip-backend's infer_sale_type/infer_industry approach per the plan).
# Extend this list as real topics reveal categories it's missing.
# --------------------------------------------------------------------------- #
_CATEGORY_KEYWORDS = {
    'festival': ['diwali', 'onam', 'christmas', 'eid', 'pongal', 'new year',
                 'vishu', 'holi', 'navratri', 'ganesh chaturthi', 'festival'],
    'announcement': ['launch', 'new product', 'introducing', 'now available',
                      'coming soon'],
    'event': ['event', 'pop-up', 'popup', 'meet', 'stall', 'exhibition'],
    'quote': ['quote', 'thought', 'inspiration'],
}


def infer_topic_category(topic):
    text = (topic or '').lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return 'general'


# --------------------------------------------------------------------------- #
# Selection — 3-layer fallback (poster-generation-plan.md §4.3): strict
# (category + format) -> format-only -> any active row for the brand.
# --------------------------------------------------------------------------- #
def list_eligible_inspirations(brand, topic=None, format=None):
    """Broader listing for a future "pick a different reference" control
    (poster plan §4.3's list_eligible_inspirations)."""
    qs = PosterInspiration.objects.filter(brand=brand, is_active=True)
    if format:
        qs = qs.filter(format=format)
    if topic:
        qs = qs.filter(topic_category=infer_topic_category(topic))
    return qs


def select_poster_inspiration(brand, topic, format=PosterFormat.SQUARE):
    """Random pick within the matching set so repeat requests for the same
    topic don't always reuse the same reference. Returns None if the brand
    has no ingested inspirations at all yet (run ingest_poster_inspirations
    first) — callers must handle that, there's nothing to imitate."""
    category = infer_topic_category(topic)

    strict = PosterInspiration.objects.filter(
        brand=brand, is_active=True, topic_category=category, format=format,
    ).order_by('?').first()
    if strict:
        return strict

    format_only = PosterInspiration.objects.filter(
        brand=brand, is_active=True, format=format,
    ).order_by('?').first()
    if format_only:
        return format_only

    return PosterInspiration.objects.filter(brand=brand, is_active=True).order_by('?').first()


def select_brand_asset(brand, tag=None):
    """Random active BrandAsset for compositing (poster plan §3 — logo/
    product/lifestyle/team/event photos). Returns None if the brand has none
    uploaded yet (or none matching `tag`) — every caller already treats a
    missing topic_asset/brand_logo as optional, so this is never a hard
    failure, just "nothing to composite this time." No inspiration-style
    3-layer fallback here: unlike PosterInspiration (which needs a specific
    topic_category+format match to make sense), a brand asset's `tag` is a
    much coarser bucket — falling back past it to "any asset of any tag"
    would risk compositing a team photo in as if it were a product shot."""
    qs = BrandAsset.objects.filter(brand=brand, is_active=True)
    if tag:
        qs = qs.filter(tag=tag)
    return qs.order_by('?').first()


# --------------------------------------------------------------------------- #
# Brief generation — headless `claude -p`, no tools (poster plan §5).
# --------------------------------------------------------------------------- #
def generate_poster_brief(brand, topic, inspiration, context_notes='', approved_copy=None, has_brand_logo=False):
    """
    Returns the brief as plain text, fed verbatim into the image prompt
    later (generate_poster_image). Raises on failure rather than
    swallowing it — same as debugger's _call_advisor; best-effort handling
    (catch, log, surface to the admin) belongs to the calling agent code,
    not this function.

    `approved_copy`, when given, is the admin-approved on-poster text (a
    Copywriter-produced Script's `raw_output` for poster_learn/poster_
    occasion/poster_event — content-generator-plan.md's Script->Designer
    contract). When set, the brief-writer is told the headline/subheadline
    are ALREADY WRITTEN and must be used verbatim (trimmed only if the
    reference layout can't fit it) — never paraphrased or invented fresh.
    Backward compatible: omitted (the default), this behaves exactly as
    before — DesignerTestAPI's existing calls never pass it.

    `has_brand_logo` — whether a real BrandAsset(tag=logo) is actually
    available to composite in (select_brand_asset's result, truthy or not).
    Gates `logo_position` alongside `inspiration.has_logo` below: asking the
    brief-writer to commit to a logo position when there's no logo image to
    place there would produce a brief the image call can't fulfill. Default
    False keeps this backward compatible with every existing caller that
    predates brand-asset selection.
    """
    if not shutil.which(settings.CLAUDE_CLI_BIN):
        raise RuntimeError(f'claude CLI not found on PATH ({settings.CLAUDE_CLI_BIN!r})')

    # Field list is dynamic based on what the reference composition can
    # actually hold (poster plan §5) — e.g. only ask for logo_position if
    # this inspiration has a logo slot, never let the model write "n/a".
    fields = ['scene', 'mood', 'colors', 'style', 'text_placement',
              'image_treatment', 'headline', 'subheadline']
    if context_notes:
        fields.append('cta_text')
    if inspiration and inspiration.has_logo and has_brand_logo:
        fields.append('logo_position')

    ref_block = (
        f"Reference poster this brief is written FOR (imitate its layout/"
        f"composition, not its content):\nDescription: {inspiration.description}\n"
        f"Why it works: {inspiration.design_language}"
        if inspiration else
        "No reference poster selected — write a brief a competent designer "
        "could execute from scratch, staying within the brand kit's visual rules."
    )

    approved_copy_block = (
        f"\nAPPROVED COPY — the headline/subheadline are ALREADY WRITTEN and "
        f"approved by the admin; do not invent alternative wording:\n{approved_copy}\n"
        if approved_copy else ""
    )
    approved_copy_rule = (
        "\n- The headline/subheadline text above is ALREADY APPROVED — copy it "
        "into those fields verbatim (trim only if the reference layout genuinely "
        "can't fit all of it), never rephrase or invent different wording."
        if approved_copy else ""
    )

    prompt = f"""Brand kit:
{brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults and say so is risky; flag this in your response)'}

{ref_block}
{approved_copy_block}
Topic: {topic}
{"Additional context (event details, angle, etc.): " + context_notes if context_notes else ""}

Write a POSTER_BRIEF with exactly these fields, one per line as `field: value`:
{', '.join(fields)}

Rules:
- Commit to a real, specific answer for every field listed above — never
  write "none"/"n/a"/"TBD", especially logo_position if it's in the list.
- headline/subheadline must be short enough to read on a phone screen in
  under a second — this is a poster, not a paragraph.
- Don't invent a brand fact, claim, or detail that isn't in the brand kit
  or the topic/context given above.{approved_copy_rule}
- End with one line starting "Constraint: " reminding the image model not
  to look like a generic stock template or obviously AI-generated art."""

    result = run_claude_cli(
        prompt,
        disallowed_tools=NO_TOOLS,
        model=settings.CONTENT_TEXT_MODEL,
        max_turns=1,
    )
    if result.get('is_error'):
        raise RuntimeError(
            f'brief generation failed (subtype={result.get("subtype")}): '
            f'{result.get("text") or "(no text)"}')
    text = (result.get('text') or '').strip()
    if not text:
        raise RuntimeError('brief generation returned no text')
    return text


# --------------------------------------------------------------------------- #
# Image generation — Gemini (poster plan §6). UNTESTED in this environment.
# --------------------------------------------------------------------------- #
_FORMAT_DIMENSIONS = {
    PosterFormat.SQUARE: (1080, 1080),
    PosterFormat.STORY: (1080, 1920),
    PosterFormat.PORTRAIT: (1080, 1350),
}
# Gemini 3 Pro Image ("Nano Banana Pro") takes aspect ratio as a structured
# GenerateContentConfig.image_config param, not a text instruction — verified
# against the current google-genai SDK docs/cookbook (googleapis/python-genai
# README + Google Cloud's Gemini 3 Pro Image docs, Sept 2026). Valid values:
# 1:1, 3:2, 2:3, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 — our three formats all
# map cleanly.
_FORMAT_ASPECT_RATIOS = {
    PosterFormat.SQUARE: '1:1',
    PosterFormat.STORY: '9:16',
    PosterFormat.PORTRAIT: '4:5',
}


def generate_poster_image(brief, inspiration, topic_asset=None, brand_logo=None,
                           extra_instruction=None, format=PosterFormat.SQUARE,
                           brand_name='Varthaai'):
    """
    Returns (image_bytes | None, debug_dict) — debug_dict['mime_type'] on
    success (verified live: Gemini 3 Pro Image returns image/jpeg by
    default, not PNG). Never raises — best-effort per poster plan §6 ("no
    equivalent fallback artifact for a poster tool ... report the
    failure"). debug_dict always has enough to diagnose why, success or
    failure — poster plan §11's "debug JSON on every attempt".

    `topic_asset`/`brand_logo` are BrandAsset instances or None.
    """
    width, height = _FORMAT_DIMENSIONS.get(format, _FORMAT_DIMENSIONS[PosterFormat.SQUARE])
    debug = {
        'model': settings.GEMINI_POSTER_MODEL,
        'format': format,
        'dimensions': f'{width}x{height}',
        'inspiration_id': inspiration.id if inspiration else None,
        'has_topic_asset': bool(topic_asset and topic_asset.image),
        'topic_asset_id': topic_asset.id if topic_asset else None,
        'has_logo': bool(brand_logo and brand_logo.image),
        'brand_logo_id': brand_logo.id if brand_logo else None,
        'extra_instruction': extra_instruction or '',
    }

    if not GENAI_AVAILABLE:
        debug['error'] = 'google-genai package not installed'
        logger.warning('generate_poster_image: %s', debug['error'])
        return None, debug
    if not settings.GEMINI_API_KEY:
        debug['error'] = 'GEMINI_API_KEY not configured'
        logger.warning('generate_poster_image: %s', debug['error'])
        return None, debug

    prompt_lines = [
        f"Replicate the QUALITY and STYLE of the reference image, not its "
        f"specific content — this is a new poster for {brand_name}, not a "
        f"copy of the reference's subject.",
        f"Reference design language: {inspiration.design_language}" if inspiration else '',
        'Brief:',
        brief,
        'Do not look like a generic stock template or obviously AI-generated '
        'art — this must look like a real brand designed it.',
    ]
    if extra_instruction:
        prompt_lines.append(
            'ADDITIONAL USER INSTRUCTION — override anything above where '
            f'they conflict: {extra_instruction}')
    prompt = '\n\n'.join(line for line in prompt_lines if line)
    debug['prompt'] = prompt
    aspect_ratio = _FORMAT_ASPECT_RATIOS.get(format, _FORMAT_ASPECT_RATIOS[PosterFormat.SQUARE])
    debug['aspect_ratio'] = aspect_ratio

    try:
        parts = []
        if inspiration and inspiration.image:
            parts.append(_image_part(inspiration.image))
        if topic_asset and topic_asset.image:
            parts.append(_image_part(topic_asset.image))
        if brand_logo and brand_logo.image:
            parts.append(_image_part(brand_logo.image))
        parts.append(prompt)

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=settings.GEMINI_POSTER_MODEL,
            contents=parts,
            # response_modalities must explicitly include IMAGE or the model
            # can return text only — this was missing before and is the
            # most likely reason an earlier untested version of this call
            # would have silently failed to return an image at all.
            config=genai_types.GenerateContentConfig(
                response_modalities=['TEXT', 'IMAGE'],
                image_config=genai_types.ImageConfig(
                    aspect_ratio=aspect_ratio,
                    image_size='2K',
                ),
            ),
        )
        image_bytes, mime_type = _extract_image_bytes(response)
        if image_bytes is None:
            debug['error'] = 'Gemini returned no image data'
            return None, debug
        # Verified live: Gemini 3 Pro Image returns image/jpeg by default,
        # NOT image/png despite the "png_bytes" naming elsewhere in this
        # module's docstrings — record the real mime type rather than
        # assuming, so callers (e.g. building a data: URI, or eventually
        # saving to PosterAsset.image) use the correct one.
        debug['mime_type'] = mime_type
        return image_bytes, debug
    except Exception as exc:
        logger.exception('generate_poster_image failed')
        debug['error'] = str(exc)
        return None, debug


def _image_part(image_field):
    """Reads a Django ImageField's bytes into a genai image Part, with the
    mime type derived from the file's actual extension rather than assumed —
    ImageField doesn't enforce a single format, so a hardcoded 'image/png'
    would mislabel a stored .jpg."""
    mime_type = mimetypes.guess_type(image_field.name)[0] or 'image/jpeg'
    image_field.open('rb')
    try:
        data = image_field.read()
    finally:
        image_field.close()
    return genai_types.Part.from_bytes(data=data, mime_type=mime_type)


def _extract_image_bytes(response):
    """Returns (bytes, mime_type) or (None, None)."""
    for candidate in getattr(response, 'candidates', None) or []:
        content = getattr(candidate, 'content', None)
        for part in getattr(content, 'parts', None) or []:
            inline = getattr(part, 'inline_data', None)
            if inline and getattr(inline, 'data', None):
                return inline.data, getattr(inline, 'mime_type', None) or 'image/jpeg'
    return None, None


# --------------------------------------------------------------------------- #
# Daily orchestration + versioning/edit loop (content-generator-plan.md §13
# Phase 3, poster-generation-plan.md §7). Gated on an approved Script per the
# Script->Designer contract: for poster_learn/poster_occasion/poster_event,
# a Script's `raw_output` IS the approved on-poster copy, passed through as
# `approved_copy` so the brief-writer renders it rather than inventing its
# own headline/subheadline.
# --------------------------------------------------------------------------- #
_POSTER_CONTENT_TYPES = (
    ContentSeries.ContentType.POSTER_LEARN,
    ContentSeries.ContentType.POSTER_OCCASION,
    ContentSeries.ContentType.POSTER_EVENT,
)

# content-generator-plan.md §13 Phase 3: poster generation "runs 2 days
# before publish" — applied uniformly across all three poster content types
# for v1 rather than a per-series lead time (none of them specify a
# different number). Only an upper bound: an item whose planned_date has
# already passed is still eligible (a late run shouldn't skip it), this just
# stops run_daily from generating weeks-early posters the moment a Script
# happens to get approved far ahead of schedule.
POSTER_LEAD_DAYS = 2

# Format-default rule: poster_learn/poster_occasion always publish square
# (1:1) — that's the only shape these two series have ever used. poster_event
# has no fixed shape (a launch-party poster and a "join our webinar" story
# graphic are both "events"), so it defaults to square too UNLESS the admin's
# context_notes (the one place event specifics are ever supplied, per
# content-generator-plan.md §1 — "never inferred") explicitly names a
# different shape via a keyword match. Simple and legible over clever; revisit
# if poster_event's real usage shows a better default.
_FORMAT_HINT_KEYWORDS = {
    PosterFormat.STORY: ['9:16', 'story format', 'reel format', 'stories', 'instagram story'],
    PosterFormat.PORTRAIT: ['4:5', 'portrait'],
}


def _resolve_poster_format(plan_item):
    if plan_item.content_type != ContentSeries.ContentType.POSTER_EVENT:
        return PosterFormat.SQUARE
    text = (plan_item.context_notes or '').lower()
    for fmt, keywords in _FORMAT_HINT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return fmt
    return PosterFormat.SQUARE


def _next_poster_version(plan_item):
    latest = PosterAsset.objects.filter(plan_item=plan_item).order_by('-version').first()
    return (latest.version + 1) if latest else 1


def _ensure_poster_review_action_item(plan_item):
    ActionItem.objects.get_or_create(
        plan_item=plan_item, kind=ActionItem.Kind.POSTER_REVIEW, status=ActionItem.Status.OPEN,
        defaults={'title': f'Review poster: {plan_item.working_title}'},
    )


def _save_poster_image(poster, image_bytes, debug):
    """Attaches generated image bytes to an (unsaved) PosterAsset using the
    REAL extension from debug['mime_type'] — Gemini 3 Pro Image returns
    image/jpeg by default, not PNG (see generate_poster_image's docstring);
    hardcoding `.png` here would reintroduce the exact bug already found and
    fixed once for the reference-image read path."""
    ext = mimetypes.guess_extension((debug or {}).get('mime_type') or '') or '.jpg'
    poster.image.save(
        f'poster_{poster.plan_item_id}_v{poster.version}{ext}',
        ContentFile(image_bytes), save=False,
    )


def _latest_approved_script(plan_item):
    return plan_item.scripts.filter(status=Script.Status.APPROVED).order_by('-version').first()


def run_daily(brand=None):
    """
    Daily poster-generation sweep (content-generator-plan.md §13 Phase 3).

    For every PlanItem whose content_type is a poster type (poster_learn,
    poster_occasion, poster_event) with a latest-by-version Script whose
    status is APPROVED, generate a poster UNLESS one has already been
    attempted for this item and either (a) is awaiting/has passed review
    (status NEEDS_REVIEW or APPROVED — don't regenerate something already
    in the queue), or (b) failed to produce an image at all. (b) is a
    deliberate choice: a failed Gemini call (bad key, model outage, content
    policy block) should NOT retry-storm on every daily run — it counts as
    "already attempted" same as a successful one. The admin can explicitly
    ask for a fresh attempt via the review UI's `retry` action (POST
    action=retry -> regenerate_poster), which creates a new version and so
    un-sticks this gate on the next daily run too.

    A PlanItem whose only PosterAsset history is CHANGES_REQUESTED is NOT
    considered "already attempted" — that's the admin asking for a new
    take, so a fresh automated attempt is allowed the next time this runs
    (in addition to the admin's own explicit `regenerate` action).

    Also gated on POSTER_LEAD_DAYS: an item whose planned_date is more than
    2 days out is left alone even if its Script is already approved (early
    approval shouldn't mean early generation) — it becomes eligible the
    moment today crosses that 2-day window, same "re-checked every run"
    idea as the Copywriter's own prep-lead-days gate.

    Brand-scoped via `plan_item.plan.brand` when `brand` is given (matches
    the Copywriter sibling task's own `run_daily(brand=None)` idiom).

    Returns {'generated': int, 'failed': int, 'skipped': int} — 'generated'
    counts attempts that produced a real image, 'failed' counts attempts
    that ran (brief and/or image call) but produced nothing, both of which
    still persist a PosterAsset so nothing vanishes silently (poster-
    generation-plan.md §11's "always report the failure"). Not-yet-in-window
    items don't count toward any of these three — they're simply not
    iterated, same as an item with no approved Script at all.
    """
    cutoff = timezone.localdate() + timedelta(days=POSTER_LEAD_DAYS)
    qs = (
        PlanItem.objects.filter(content_type__in=_POSTER_CONTENT_TYPES, planned_date__lte=cutoff)
        .select_related('plan__brand')
    )
    if brand is not None:
        qs = qs.filter(plan__brand=brand)

    summary = {'generated': 0, 'failed': 0, 'skipped': 0}

    for plan_item in qs:
        script = _latest_approved_script(plan_item)
        if not script:
            continue  # nothing approved to gate on yet — not a "skip", just not eligible

        latest_poster = PosterAsset.objects.filter(plan_item=plan_item).order_by('-version').first()
        if latest_poster and (
            latest_poster.status in (PosterAsset.Status.NEEDS_REVIEW, PosterAsset.Status.APPROVED)
            or not latest_poster.image
        ):
            summary['skipped'] += 1
            continue

        brand_obj = plan_item.plan.brand
        format = _resolve_poster_format(plan_item)
        inspiration = select_poster_inspiration(brand_obj, plan_item.working_title, format=format)
        topic_asset = select_brand_asset(brand_obj, tag=BrandAsset.Tag.PRODUCT)
        brand_logo = select_brand_asset(brand_obj, tag=BrandAsset.Tag.LOGO)

        try:
            brief = generate_poster_brief(
                brand_obj, plan_item.working_title, inspiration,
                context_notes=plan_item.context_notes, approved_copy=script.raw_output,
                has_brand_logo=bool(brand_logo),
            )
        except Exception as exc:
            logger.exception('run_daily: brief generation failed for PlanItem %s', plan_item.id)
            PosterAsset.objects.create(
                plan_item=plan_item, format=format, inspiration=inspiration,
                brief={}, generation_metadata={'error': f'brief generation failed: {exc}'},
                version=_next_poster_version(plan_item), status=PosterAsset.Status.NEEDS_REVIEW,
            )
            _ensure_poster_review_action_item(plan_item)
            summary['failed'] += 1
            continue

        image_bytes, debug = generate_poster_image(
            brief, inspiration, topic_asset=topic_asset, brand_logo=brand_logo,
            format=format, brand_name=brand_obj.name,
        )

        poster = PosterAsset(
            plan_item=plan_item, format=format, inspiration=inspiration,
            brief={'text': brief}, generation_metadata=debug,
            version=_next_poster_version(plan_item), status=PosterAsset.Status.NEEDS_REVIEW,
        )
        if image_bytes:
            _save_poster_image(poster, image_bytes, debug)
        poster.save()
        _ensure_poster_review_action_item(plan_item)

        summary['generated' if image_bytes else 'failed'] += 1

    return summary


def regenerate_poster(plan_item, instruction='', inspiration_id=None):
    """
    Poster plan §7's "Preview/edit endpoint" — re-runs brief+image
    generation for `plan_item`'s latest APPROVED Script (raises if there
    isn't one — same contract as the rest of this codebase's agent
    functions, e.g. planner.regenerate_item), optionally steered by a
    free-text `instruction` (appended verbatim to generate_poster_image's
    existing `extra_instruction` param) and/or a different `inspiration_id`
    (must belong to the same brand as `plan_item.plan.brand` — raises
    ValueError otherwise, never silently falls back to a random one).

    Always creates a NEW PosterAsset row (version = 1 + the current max
    version for this plan_item) — never mutates an existing reviewed row,
    so a rejected/approved version stays exactly as the admin left it.

    Unlike generate_poster_image itself, THIS function's image-generation
    failure is still captured, not raised — mirrors run_daily's "always
    persist and report" rule so a regenerate attempt that fails is visible
    on the review page (a new NEEDS_REVIEW row with image=None) rather than
    the admin seeing nothing happen. Only a missing-approved-Script or an
    invalid inspiration_id raises (a real caller/programming error, not a
    generation-time failure).
    """
    script = _latest_approved_script(plan_item)
    if not script:
        raise RuntimeError(
            'This plan item has no approved Script yet — nothing to render a poster from.')

    brand = plan_item.plan.brand
    format = _resolve_poster_format(plan_item)

    if inspiration_id is not None:
        inspiration = PosterInspiration.objects.filter(id=inspiration_id, brand=brand).first()
        if not inspiration:
            raise ValueError('inspiration_id does not belong to this brand (or does not exist).')
    else:
        inspiration = select_poster_inspiration(brand, plan_item.working_title, format=format)

    topic_asset = select_brand_asset(brand, tag=BrandAsset.Tag.PRODUCT)
    brand_logo = select_brand_asset(brand, tag=BrandAsset.Tag.LOGO)

    brief = generate_poster_brief(
        brand, plan_item.working_title, inspiration,
        context_notes=plan_item.context_notes, approved_copy=script.raw_output,
        has_brand_logo=bool(brand_logo),
    )
    image_bytes, debug = generate_poster_image(
        brief, inspiration, topic_asset=topic_asset, brand_logo=brand_logo,
        extra_instruction=(instruction or None),
        format=format, brand_name=brand.name,
    )

    poster = PosterAsset(
        plan_item=plan_item, format=format, inspiration=inspiration,
        brief={'text': brief}, generation_metadata=debug,
        version=_next_poster_version(plan_item), status=PosterAsset.Status.NEEDS_REVIEW,
    )
    if image_bytes:
        _save_poster_image(poster, image_bytes, debug)
    poster.save()
    _ensure_poster_review_action_item(plan_item)
    return poster
