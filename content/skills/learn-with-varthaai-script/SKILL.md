---
name: learn-with-varthaai-script
description: Produces the on-screen phrase card content for a "Learn with Varthaai" reel — the Bengaluru-focused language series teaching one everyday phrase per episode in Kannada, Malayalam, and English. Trigger on "Learn with Varthaai for [situation/phrase]", "write a Learn with Varthaai script", or close variants. Takes a situation (e.g. "asking for extra chutney", "haggling at a shop") or a specific English phrase and produces the Kannada + Malayalam + English trio with phonetic transliteration, verified via web search rather than recalled, plus a short situational hook. Does NOT write narrated voiceover, a shot list, or a full script — this is a single-phrase content card, matching the format's low-production, one-phrase-per-reel structure.
---

# Learn with Varthaai — Phrase Card Generator

Learn with Varthaai teaches one everyday phrase per reel in Kannada, Malayalam, and
English, aimed at a Bengaluru audience. It's utility content — short, saveable,
practical — not a narrated language lesson. This skill produces the phrase card for
one episode: the trio of translations, phonetic spelling, and a one-line situational
hook. It does not write a script, hook sequence, or shot list.

## Required input

Ask for whatever's missing — don't assume:

1. **Either** a situation/context (e.g. "ordering food", "asking an auto driver for
   a discount", "thanking someone") **or** a specific English phrase to translate.
2. Optional: tone — most of this series should default to casual/everyday register
   (how people actually speak), not formal or textbook phrasing, unless Ajwad says
   otherwise.

If given only a broad theme (e.g. "food"), propose 2-3 concrete phrase options within
that theme rather than picking one silently, since the situational specificity is
what makes the phrase useful/memorable.

## Step 1 — Verify, don't recall

Kannada and Malayalam translations, phonetic spelling, and formal/informal register
are all things Ajwad is not fluent enough in Kannada to eyeball-check, and getting
one wrong on a public reel undermines the whole series' credibility. **Web search to
verify** the Kannada translation and transliteration rather than relying on memory,
even for phrases that seem simple. Malayalam is Ajwad's own language, so it needs
less verification, but still sanity-check uncommon or regional phrasing.

Also verify:
- **Register**: Kannada in particular has a strong formal/informal distinction. Flag
  which register the phrase uses and whether that's the natural choice for the
  situation (e.g. talking to an auto driver vs. an elder).
- **Script correctness**: get the actual Kannada/Malayalam script right, not just a
  transliteration guess.

## Step 2 — Build the phrase card

Produce all of the following for the chosen phrase:

- **English**: the phrase in plain English.
- **Kannada**: script + phonetic transliteration (Roman letters, stressed for how it
  actually sounds when spoken, not academic IPA).
- **Malayalam**: script + phonetic transliteration, same style.
- **Situational hook**: one line on when/why you'd use this — the framing that makes
  it a "practical situation" rather than a dry vocabulary card (e.g. "Use this next
  time your auto driver quotes double the meter").
- **Register note**: casual/formal, and who you'd say it to.

## Step 3 — Output

```
LEARN WITH VARTHAAI — [situation/phrase title]

Hook: <one-line situational framing>

English: <phrase>
Kannada: <script> — "<phonetic>"
Malayalam: <script> — "<phonetic>"

Register: <casual/formal — who to use it with>
Source(s): <what was checked to verify the Kannada — and Malayalam if uncommon>
```

If Ajwad asked for a theme rather than a specific phrase, present 2-3 candidate
phrases first (English only, no full card yet) and let him pick before doing the
full verification + card for the chosen one — don't spend a search pass on options
that won't get used.

## What this skill explicitly does NOT do

- Does not write narrated voiceover, a hook-into-full-script sequence, or a
  shot/edit list — that's a different format from Varthaai Varthaanm or The
  Varthaai Verdict; this series is a single on-screen phrase card per reel.
- Does not skip verification for Kannada, even for phrases that seem obvious —
  partial/guessed transliteration is treated as unreliable for a public-facing card.
- Does not default to formal/textbook phrasing — this series is about how people
  actually talk, unless Ajwad asks for formal register specifically.
