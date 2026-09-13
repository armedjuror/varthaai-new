---
name: varthaai-inside-questions
description: Produces the Q&A question set for a Varthaai Inside episode — the Malayalam-language interview series with co-founder Saad about Varthaai operations. Trigger on "Varthaai Inside questions for [topic]", "prep Varthaai Inside on [topic]", or close variants. Splits every topic into two handling modes — factual/compliance topics (certifications, FSSAI, legal/regulatory requirements) get web-searched, verified, camera-ready Q&A pairs with sources; experiential/opinion topics (retail hurdles, team stories, day-to-day war stories, "what surprised you") get questions plus answer angles only — never a scripted answer, since those are meant to be spontaneous on camera. Does NOT write narrated voiceover, hooks, or a shot list — this is an interview prep sheet, not a script.
---

# Varthaai Inside — Episode Question Generator

Varthaai Inside is a Malayalam-language interview series with co-founder Saad,
pulling back the curtain on how Varthaai actually runs. This skill prepares the
question set for one episode. It does not write a script, narration, or shot list —
Ajwad and Saad talk live on camera; this skill only prepares what gets asked (and,
where appropriate, verified answers to ask around).

## Required input

Ask for whatever's missing — don't assume:

1. **Topic** for the episode (e.g. "FSSAI & quality certifications", "retail
   partnership hurdles", "banana sourcing").
2. If the topic is broad or ambiguous, ask whether Ajwad wants a **single-topic deep
   dive** (4-6 questions, one theme, more educational) or a **rapid-fire** mix (a few
   questions each across 2-3 lighter topics).

## Step 1 — Classify the topic

Every question in the set falls into one of two modes. Classify the *topic itself*
first, then check each individual question against the same test, since a broad
topic can still contain a mix:

- **Factual/Compliance** — anything involving regulations, certifications, official
  processes, standards, or numbers that could be verified or gotten wrong (FSSAI,
  ISO/quality certs, labeling law, GST/registration mechanics, food safety
  standards). Getting these wrong on camera damages credibility.
- **Experiential/Opinion** — anything involving feelings, decisions, judgment calls,
  or "how did it go" (retail hurdles, vendor relationships, team disagreements,
  what surprised the founders, lessons learned). These should stay spontaneous —
  scripting them would make the interview feel fake.

If a topic mixes both (e.g. "quality certifications" naturally leads to "what was
the hardest cert to get"), split it: the requirement questions are Factual, the
"what was hard about it" follow-up is Experiential.

## Step 2 — Build the question set

### For Factual/Compliance questions

1. **Web search** to verify current requirements — don't rely on memory, since
   regulations and certification requirements shift and incomplete recall reads as
   confidently wrong on camera. Search per sub-topic (e.g. "FSSAI license
   requirements packaged food India 2026", not one combined query).
2. Write a camera-ready answer: 2-3 sentences, plain spoken language (not a legal
   quote), accurate and current as of the search.
3. Note the source(s) used so Ajwad/Saad can double check before filming if they want.
4. If search results conflict or a requirement seems to have changed recently, flag
   that explicitly rather than picking one silently.

### For Experiential/Opinion questions

1. Write the question as it'd actually be asked in Malayalam-interview style —
   conversational, not clinical ("What was the toughest part of getting your first
   retail partner on board?" not "Describe retail partnership challenges").
2. Do **not** write or imply an answer. Instead give 2-3 short **answer angles** —
   directions the question could go — so whoever's answering has a mental on-ramp
   without being scripted. E.g. for "retail hurdles": shelf-space negotiation,
   payment terms, rejection stories.
3. Optionally add one **follow-up probe** to keep the exchange conversational rather
   than one-and-done (e.g. "What would you do differently next time?").
4. Never invent a specific anecdote, number, or outcome Ajwad/Saad hasn't told you —
   angles are directional prompts, not fabricated stories.

## Step 3 — Output

Always structure the output by question, tagged with its mode:

```
VARTHAAI INSIDE — [Topic]

Q1 [Factual] — <question>
A: <2-3 sentence verified answer>
Source: <source(s)>

Q2 [Experiential] — <question>
Angles: <2-3 short directions>
Follow-up: <optional probe question>

...
```

Close with one **wrap-up question** for the episode — something like "so what
should someone starting out in food know about this?" — giving the episode a
takeaway rather than ending on a raw anecdote. This is always Experiential (no
scripted answer).

## What this skill explicitly does NOT do

- Does not write narrated voiceover, a hook, cold-open, or b-roll/shot list — that's
  a different format from Varthaai Varthaanm or The Varthaai Verdict.
- Does not script or predict answers for Experiential questions — only angles/probes.
- Does not skip web search for Factual questions, even on topics Ajwad/Saad already
  know well — partial recall is treated as unreliable for on-camera claims.
- Does not name or imply which founder answers which question — that's decided live.
