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
  generate_poster_brief(brand, topic, inspiration, context_notes='') -> str
  generate_poster_image(brief, inspiration, ...) -> tuple[bytes | None, dict]
"""
import logging
import shutil

from django.conf import settings

from core.claude_cli import NO_TOOLS, run_claude_cli

from content.models import PosterFormat, PosterInspiration

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


# --------------------------------------------------------------------------- #
# Brief generation — headless `claude -p`, no tools (poster plan §5).
# --------------------------------------------------------------------------- #
def generate_poster_brief(brand, topic, inspiration, context_notes=''):
    """
    Returns the brief as plain text, fed verbatim into the image prompt
    later (generate_poster_image). Raises on failure rather than
    swallowing it — same as debugger's _call_advisor; best-effort handling
    (catch, log, surface to the admin) belongs to the calling agent code,
    not this function.
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
    if inspiration and inspiration.has_logo:
        fields.append('logo_position')

    ref_block = (
        f"Reference poster this brief is written FOR (imitate its layout/"
        f"composition, not its content):\nDescription: {inspiration.description}\n"
        f"Why it works: {inspiration.design_language}"
        if inspiration else
        "No reference poster selected — write a brief a competent designer "
        "could execute from scratch, staying within the brand kit's visual rules."
    )

    prompt = f"""Brand kit:
{brand.brand_kit or '(no brand kit written yet — use generic, tasteful defaults and say so is risky; flag this in your response)'}

{ref_block}

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
  or the topic/context given above.
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
    Returns (png_bytes | None, debug_dict). Never raises — best-effort per
    poster plan §6 ("no equivalent fallback artifact for a poster tool ...
    report the failure"). debug_dict always has enough to diagnose why,
    success or failure — poster plan §11's "debug JSON on every attempt".

    `topic_asset`/`brand_logo` are BrandAsset instances or None.
    """
    width, height = _FORMAT_DIMENSIONS.get(format, _FORMAT_DIMENSIONS[PosterFormat.SQUARE])
    debug = {
        'model': settings.GEMINI_POSTER_MODEL,
        'format': format,
        'dimensions': f'{width}x{height}',
        'inspiration_id': inspiration.id if inspiration else None,
        'has_topic_asset': bool(topic_asset and topic_asset.image),
        'has_logo': bool(brand_logo and brand_logo.image),
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
        image_bytes = _extract_image_bytes(response)
        if image_bytes is None:
            debug['error'] = 'Gemini returned no image data'
            return None, debug
        return image_bytes, debug
    except Exception as exc:
        logger.exception('generate_poster_image failed')
        debug['error'] = str(exc)
        return None, debug


def _image_part(image_field):
    """Reads a Django ImageField's bytes into a genai image Part."""
    image_field.open('rb')
    try:
        data = image_field.read()
    finally:
        image_field.close()
    return genai_types.Part.from_bytes(data=data, mime_type='image/png')


def _extract_image_bytes(response):
    for candidate in getattr(response, 'candidates', None) or []:
        content = getattr(candidate, 'content', None)
        for part in getattr(content, 'parts', None) or []:
            inline = getattr(part, 'inline_data', None)
            if inline and getattr(inline, 'data', None):
                return inline.data
    return None
