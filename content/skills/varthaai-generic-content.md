---
name: varthaai-generic-content
description: Shared format/structure rules for Varthaai content that isn't one of the four scripted reel series — occasion posters, event posters, and blog posts. Trigger when the Copywriter Agent is producing a `PlanItem` whose `content_type` is `poster_occasion`, `poster_event`, or `blog`. Does NOT apply to `reel_varthaanm`, `reel_verdict`, `reel_learn`, or `reel_inside` — those each have their own dedicated skill and keep using it unchanged. This skill carries no creative judgment of its own; it dispatches to the right output shape and defers entirely to the Brand Kit (loaded fresh from `Brand.brand_kit`) for voice, tone-register choice, and visual rules.
---

# Varthaai Generic Content — Poster & Blog Copy Rules

Three `content_type`s share one lightweight rule set because none of them need a
bespoke creative skill the way the four reel series do — what they need is the
right **length/structure for the format**, with tone coming entirely from the
Brand Kit. Read `Brand.brand_kit` fresh at the start of every run (never cached,
never assumed from a previous run) — it holds the two tone registers
(warm/heritage vs. meme/trend-jacking), the Blog voice section, and the Verdict
confidentiality rule this skill defers to below.

## Step 1 — Confirm `content_type` and required inputs

The `PlanItem` already carries `content_type`. Don't re-derive it — just check
that the inputs that type needs are actually present before writing:

| `content_type` | Needs beyond the topic | If missing |
|---|---|---|
| `poster_occasion` | Nothing else — the topic/occasion name is enough. | N/A — this type never blocks on input. |
| `poster_event` | Concrete specifics: what, date, time, venue-or-link, and the CTA. These live in the `PlanItem`'s context/notes field, supplied by the Planner or an admin. | **Never invent or infer these.** If any are missing, don't draft — this should already be an open `input_needed` `ActionItem` upstream; don't paper over a gap by guessing a plausible-sounding date or venue. |
| `blog` | Topic, plus optional angle/keywords if given. | Angle/keywords are a nice-to-have, not a blocker — write from the topic alone if that's all there is. |

## Step 2 — Pick the register (posters only)

Not every occasion is the same kind of occasion. Use the Brand Kit's two
registers as the actual decision:

- **A recurring festival, cultural observance, or genuinely warm moment**
  (the kind of thing that happens every year, not tied to a fleeting trend) →
  **warm/heritage register**. Short, sincere, unhurried.
- **A live cultural/pop-culture moment** (something currently circulating —
  a meme, a release, a viral format) → **meme/trend-jacking register**. The
  Brand Kit deliberately does not fix this to any one past format — build the
  joke around whatever's actually trending right now (per the Planner's
  `TrendFlag` input or occasion research), not a recycled reference.

If a topic doesn't clearly fall into either, default to whichever register the
Brand Kit's current examples lean toward for that broad category, and flag the
ambiguity in the draft's review notes rather than silently picking one.

`poster_event` posters (an actual announcement with a date/venue/CTA) skew
warm/plain regardless of register — the job is clarity on the specifics, not a
joke.

## Step 3 — Write to the shape

**`poster_occasion`** — one-liner or minimal, **max 2–3 lines total**. This is
poster copy, not a caption essay: a headline plus at most one supporting line.
No CTA needed unless the occasion genuinely calls for one (e.g. a limited-time
mention) — don't manufacture urgency that isn't there.

**`poster_event`** — still short (posters aren't read like paragraphs), but
must state the concrete specifics plainly: what it is, when, where (venue or
link), and the CTA. Prioritize the reader being able to act on the poster
(show up, click, remember the date) over cleverness. A few lines is fine if
that's what fitting the real details actually takes — don't compress a real
venue/time down to the point of ambiguity just to hit a word count.

**`blog`** — long-form, structured and paced per the Brand Kit's "Blog voice"
section: more explanatory/SEO-aware than poster or reel copy, but still not
corporate. Open from a concrete, specific image (per the Brand Kit's guidance
on openings) rather than a generic abstraction. Reasonable length for the
platform (a few hundred to ~1000 words, not a thin 150-word stub padded to
look substantial, and not a 3000-word essay for a snack brand's blog).

## Step 4 — Confidentiality check

If the topic touches Varthaai Verdict material in any way (a blog recapping a
Verdict episode, a poster referencing a comparison) — the Brand Kit's
confidentiality rule applies here too: reviewed competitors' actual names stay
internal-only. Never surface them in blog or poster copy.

## Step 5 — Output

```
CONTENT_TYPE: <poster_occasion | poster_event | blog>
REGISTER: <warm/heritage | meme/trend-jacking | n/a — event>

<the copy itself, per the shape rules above>

[If poster_event: confirm what/date/time/venue-or-link/CTA are all present and
verbatim from the PlanItem's context — don't restate anything not supplied.]
```

This lands as a `Script` draft against the `PlanItem`, same review gate
(`draft → needs_review → approved`) as every other content type — nothing
here skips the approval step just because it's the "simple" path.

## What this skill explicitly does NOT do

- Does not touch `reel_varthaanm`, `reel_verdict`, `reel_learn`, or
  `reel_inside` — those are exactly per their own skill, unchanged, per the
  "if it's a script for a reel, it should be according to the skill" rule.
- Does not invent event specifics (date/venue/CTA) to fill a gap — that's an
  input-needed condition, not a creative one.
- Does not pick the meme/trend-jacking register and then reach for a specific
  past joke/format as a template — the register is a voice choice, not a
  format library to replay.
- Does not write image/visual briefs — that's the Designer Agent's job
  (`generate_poster_brief`), working from this skill's approved copy plus the
  Brand Kit's visual-identity section separately.