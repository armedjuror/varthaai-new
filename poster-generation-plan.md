# Varthaai Poster Generation — System Plan

Adapted from zip-backend's AI hero-image pipeline (`base/hero_image_generator.py`,
`base/hero_inspirations.py`, `base/generate_html_utils.py`). That system composes
personalized email hero banners by feeding Gemini a curated reference design + the
brand's own photo/logo + a text brief written by an LLM. This plan reuses the same
shape for **topic → poster** generation instead of **campaign → hero banner**.

Core idea carried over: **don't ask the image model to invent a design from
scratch.** Give it a real, high-quality reference poster to imitate stylistically,
the brand's actual assets to composite in, and a precise text brief for what to
say — then let it composite. This is why the zip-backend heroes look designed
rather than "AI-generated."

---

## 1. Mapping from the zip-backend system

| zip-backend concept | Varthaai equivalent |
|---|---|
| `Brand` + `BrandWebsiteExtractedData.brand_kit_s3_url` (scraped brand kit markdown) | **Varthaai Brand Kit** — authored once, markdown, stored in S3/repo |
| `HeroInspiration` table (curated reference hero images, tagged `sale_type`/`industry`) | **PosterInspiration** table — your past posters, tagged by `topic_category` / `occasion` / `format` |
| `upload_hero_inspirations` mgmt command (Gemini-vision auto-captions each reference image) | **`ingest_poster_inspirations`** command — same idea, run once over your existing poster archive |
| Brand's round-robin product photo + logo | **Brand asset library** — logo, product/lifestyle photos, any recurring visual elements |
| `select_inspiration()` (3-layer fallback: strict → partial → any) | **`select_poster_inspiration(topic, brand_kit)`** — match on inferred category, fall back progressively |
| Claude writes a `HERO_IMAGE_BRIEF` HTML comment | An LLM call writes a **`POSTER_BRIEF`** (headline, subheadline, CTA/date/details, mood, colors, layout) from the topic + brand kit |
| `generate_hero_image()` — Gemini image-edit call with (reference, brand photo, logo, prompt) | **`generate_poster_image()`** — same Gemini call shape, swap "brand product photo" for "topic-relevant asset" |
| `_upload_hero_to_s3` + `hero_debug.json` | Same: upload PNG, persist a debug JSON of exactly what went into the prompt (critical for tuning quality) |
| `HeroVersion` model (versioned, soft-deletable pool per StrategyDoc) | **`PosterVersion`** model — versioned pool per poster request, supports "regenerate" / "edit with instruction" |
| `HeroPreviewView` (manual free-form-instruction regen endpoint) | **`PosterPreviewView`** — same: let a human nudge a specific generation with free text |

Everything below is written as if implementing this fresh for Varthaai — it does
not require zip-backend's codebase, just the same architecture.

---

## 2. Brand Kit (Varthaai)

A single markdown document (or JSON — markdown worked well in zip-backend because
it feeds directly into an LLM prompt) that captures everything the poster
generator needs to stay on-brand without being told again each time. Write this
once, update rarely.

**Suggested sections:**

```markdown
# Varthaai Brand Kit

## Identity
- Brand name, one-line description, tone of voice (e.g. "confident, warm, informal Telugu-English mix")
- Tagline(s) if any

## Visual identity
- Primary colors (hex codes) + when each is used
- Secondary/accent colors
- Typography: primary font family, weight conventions, fallback fonts
- Logo: S3/asset URL(s), clear-space rules, on-dark vs on-light variants
- Photography/illustration style: e.g. "warm natural light, candid, no stock-photo feel"

## Layout conventions
- Standard poster aspect ratios you actually publish in (e.g. 1:1 for Instagram feed,
  9:16 for stories/reels, 4:5 for feed portrait) — list ALL formats you need, since
  each is effectively a different "shape" of brief
- Logo placement convention (e.g. always top-left, always same size)
- Any mandatory footer/legal/handle text

## Audience & voice
- Who the poster talks to
- Words/phrases to use or avoid

## Asset inventory (pointers, not the assets themselves)
- Logo files → asset library ref
- Product/team photos → asset library ref
- Icon set, if any
```

This plays the exact role of `brand_kit_md` in zip-backend (`_fetch_brand_kit_from_s3`,
consumed by `infer_industry()` and stuffed into every prompt). Keep it in one place
(a repo file or a small S3 object) and load it fresh on every generation call —
don't bake it into code.

---

## 3. Brand asset library

Separate from the brand kit doc: the actual binary assets.

- **Logo(s)** — at least one on-transparent PNG, ideally light + dark variants.
- **Photo library** — product shots, people/lifestyle photos, anything that might
  serve as "the centerpiece image" the way `brand_photo_bytes` does for hero
  generation. Tag each with what it's suitable for (product, event, lifestyle,
  team, etc.) so topic → asset selection can be automatic later; a flat folder +
  manual pick is fine to start.
- Store as S3 objects (or any static host) with stable URLs — the generator only
  needs `fetch_image_bytes(url)`, mirroring `hero_image_generator.fetch_image_bytes`.

No need for a scrape pipeline like zip-backend's `BrandWebsiteExtractedData` —
that existed because zip-backend onboards *other companies'* brands
automatically. For a single brand you maintain, just hand-curate this once.

---

## 4. Poster Inspiration Library (your past posters)

This is the direct analog of `HeroInspiration` — and the highest-leverage piece,
since it's what makes new posters look like *your* posters rather than generic
AI output.

### 4.1 Data model — `PosterInspiration`

```python
class PosterInspiration(models.Model):
    source_label = models.CharField(max_length=100)       # e.g. "Varthaai — Diwali 2025"
    image_url = models.URLField(max_length=500)
    description = models.TextField()        # what's in it, concretely (LLM-authored)
    design_language = models.TextField()    # why it works — typography/mood/composition
    topic_category = models.CharField(max_length=80, db_index=True)   # e.g. "festival", "announcement", "quote", "event"
    format = models.CharField(max_length=20, db_index=True)  # "1:1" | "9:16" | "4:5"
    tags = models.JSONField(default=list)   # freeform: "style:bold_type", "mood:festive"
    has_logo = models.BooleanField(null=True, default=None)
    score = models.IntegerField(default=80)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['topic_category', 'format'])]
```

### 4.2 Ingestion — `ingest_poster_inspirations` script

One-time (then incremental) script mirroring `upload_hero_inspirations.py`:

1. Point it at a folder of your existing posters (optionally organized into
   subfolders by category — folder name becomes `topic_category`, same trick as
   the hero command's `--axis`).
2. For each image: call a vision LLM (Gemini `generate_content` with the image +
   a description prompt, exactly like `_describe_via_gemini`) to produce:
   - `description` — concrete enough that a designer could recreate the composition
   - `design_language` — the "why it works"
   - `suggested_tags`
   - `score`
3. Upload the image to S3 (or wherever you're hosting assets).
4. `update_or_create` a `PosterInspiration` row keyed by `image_url` (idempotent
   re-runs).

This is a few hours of one-off work and gives you a self-describing library you
can keep adding to (drop new posters in, re-run the script) rather than manually
tagging everything by hand.

### 4.3 Selection — `select_poster_inspiration(topic, format, brand_kit_md)`

Same 3-layer fallback as `select_inspiration()`:

1. **Strict**: match `topic_category` (inferred from the requested topic via
   keyword rules, same approach as `infer_sale_type`/`infer_industry`) AND
   `format` (the aspect ratio the caller asked for).
2. **Format-only**: relax topic match, keep format.
3. **Any active row.**

Pick randomly within the matching set (`order_by('?')`) so repeated requests for
the same topic don't always reuse the same reference.

Also keep a `list_eligible_inspirations()` variant (mirrors zip-backend's) so a
"pick a different reference" UI control is possible later.

---

## 5. Topic → Brief

Given a free-text `topic` (e.g. "Diwali sale — 20% off", "New product launch:
XYZ", "Hiring — we're looking for a backend engineer"), an LLM call produces a
structured **POSTER_BRIEF**, analogous to Claude's `HERO_IMAGE_BRIEF`:

```
scene: [...]
mood: [...]
colors: [...]
style: [...]
text_placement: [...]
image_treatment: [...]
headline: [...]
subheadline: [...]
cta_text: [...]        (only if the poster needs a CTA/date/link)
cta_color: [...]
logo_position: [...]
```

Field list should be **dynamic based on the chosen inspiration**, exactly like
`build_dynamic_fields()` — e.g. only ask for `cta_text` if the reference poster
had a CTA-shaped element; only ask for `logo_position` if `has_logo` is true for
that inspiration. This keeps briefs matched to what the reference composition can
actually hold.

**Inputs to this LLM call:** the topic text, the brand kit markdown, and the
selected inspiration's `description` + `design_language` (so the brief is written
*for that specific reference's layout*, not generically).

**Model choice:** this step doesn't need image generation — any strong text LLM
works (Claude, GPT, Gemini text-only). Keep it cheap; the expensive call is the
image generation step.

---

## 6. Image generation

Direct analog of `generate_hero_image()`. Call Gemini's image model
(`gemini-3-pro-image-preview`, or whatever the current best image-editing model
is at build time) with, in order:

1. Text label + **reference poster image** (the selected `PosterInspiration`).
2. Text label + **topic-relevant brand asset** (a product photo, event photo, or
   just leave this out if the poster is text/graphic-only — not every poster
   needs a "centerpiece photo" the way every hero email does).
3. Text label + **brand logo** (if the brief calls for a `logo_position`).
4. The full text prompt — reuse `build_gemini_edit_prompt`'s structure almost
   verbatim:
   - "Replicate quality/style of the reference, not its specific content"
   - "Here is the brand's photo/logo, use it as-is"
   - "Render this exact text, this large, this placement"
   - Output spec: exact pixel dimensions matching the target format (1080×1080
     for 1:1, 1080×1920 for 9:16, etc.)
   - A closing "don't look like a stock template or AI art" instruction — this
     line does real work in the zip-backend prompt; keep it.

```python
async def generate_poster_image(
    brief: str,
    inspiration: PosterInspiration,
    inspiration_bytes: bytes,
    topic_asset_bytes: Optional[bytes],
    brand_logo_bytes: Optional[bytes],
    extra_instruction: Optional[str] = None,
    brand_name: str = "Varthaai",
) -> tuple[Optional[bytes], dict]:
    ...
```

Return `(png_bytes, debug_dict)` where `debug_dict` captures the model name,
every input part (role/mime/size), and the full text prompt — this is what let
zip-backend debug and tune hero quality after the fact via `hero_debug.json`;
you'll want the same when tuning poster quality.

**Failure handling:** best-effort, never a hard failure. If Gemini errors or
returns no image, surface that clearly (don't silently show a broken image) —
zip-backend falls back to the plain product photo because an email must still
send; for a poster tool there's no equivalent fallback artifact, so just report
the failure and let the user retry/edit the brief.

---

## 7. Versioning & manual edit loop

- **`PosterVersion`** model (mirrors `HeroVersion`): one row per generated
  attempt for a given poster request, `source` = `'generated'`, stores
  `generation_metadata = {brief, inspiration_id, topic_asset_url, logo_url}` —
  exactly the fields needed to regenerate/re-edit without re-deriving them.
- **Preview/edit endpoint** (mirrors `HeroPreviewView`): accepts a free-form
  `extra_instruction` string, optionally a different `inspiration_id` or a
  different asset, and re-runs `generate_poster_image()` with that instruction
  appended verbatim to the prompt ("ADDITIONAL USER INSTRUCTION — override
  anything above where they conflict"). This is the fast iteration loop —
  cheaper than re-running the full topic→brief step every time you want to
  nudge one detail.

---

## 8. End-to-end flow (topic in → poster out)

```
topic (+ target format, e.g. "1:1")
   │
   ▼
select_poster_inspiration(topic, format, brand_kit_md)  ──► PosterInspiration row + image bytes
   │
   ▼
generate_poster_brief(topic, brand_kit_md, inspiration)  ──► POSTER_BRIEF text (LLM #1, text-only)
   │
   ▼
fetch topic-relevant brand asset (manual pick or simple keyword match against asset tags)
fetch brand logo bytes
   │
   ▼
generate_poster_image(brief, inspiration, inspiration_bytes, asset_bytes, logo_bytes)  (LLM #2, Gemini image)
   │
   ▼
upload PNG → S3, write PosterVersion row, write debug JSON
   │
   ▼
return poster URL (+ brief + debug, for the edit UI)
```

---

## 9. Build order (suggested phases)

1. **Brand kit doc** — write it by hand (Section 2). No code needed.
2. **Asset library** — upload logo + a handful of key photos to S3, list URLs
   in a simple JSON/markdown alongside the brand kit.
3. **PosterInspiration table + ingestion script** — get 20-50 of your best past
   posters in, auto-captioned. This alone is worth doing even before the
   generation pipeline exists, since it forces you to see what categories/tags
   naturally emerge from your own archive.
4. **Brief generation** (text LLM call) — test in isolation, eyeball briefs for
   a handful of topics before wiring up image generation.
5. **Image generation call** — wire brief + inspiration + assets → Gemini →
   PNG. Get one poster shape (say 1:1) working end-to-end before adding others.
6. **Versioning + edit endpoint** — once base quality is acceptable, add the
   regenerate/edit loop.
7. **Additional formats** (9:16, 4:5, etc.) — extend once 1:1 is solid; mostly
   a matter of adding format to the inspiration filter and the output-spec line
   in the prompt.

---

## 10. Config / environment needed

- `GEMINI_API_KEY` — for both the vision-captioning step (ingestion) and the
  final image-generation step.
- `GEMINI_POSTER_MODEL` (analog of `GEMINI_HERO_MODEL`) — default to whatever
  Gemini image-editing model is current; keep it env-overridable since these
  models get replaced often.
- S3 (or equivalent) bucket + credentials for inspiration images, brand assets,
  and generated poster output.
- A text-LLM API key for the brief-generation step (can be the same Gemini key,
  or Claude/GPT if you prefer a different model for copywriting).

---

## 11. Notes carried over from zip-backend's production experience

- **Always inject the inspiration's own `description`/`design_language` into the
  image prompt**, not just into the brief-writing step — otherwise, if a user
  swaps the reference at edit-time, the brief (written for the *original*
  reference) drowns out the new one's design language. (`hero_image_generator.py`
  lines 138-148.)
- **Don't let the brief-writer skip fields that matter.** zip-backend explicitly
  forbids the brief LLM from writing "none"/"n/a" for `logo_position` when the
  inspiration has a logo slot — it must commit to a real position. Vague briefs
  produce vague composites.
- **Keep the "don't look like AI art / stock template" line in the prompt.**
  Anecdotally load-bearing for output quality.
- **Debug JSON on every attempt, success or failure.** Cheap to write, and it's
  the only way to retroactively figure out why a specific poster came out wrong
  (which reference was picked, what the exact brief said, what prompt Gemini
  actually saw).
