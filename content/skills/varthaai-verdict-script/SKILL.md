---
name: varthaai-verdict-script
description: Produces the on-camera talking-point sheet for a Varthaai Verdict reel (blind snack review) — NOT a narrated VO script. Trigger on "write script for Varthaai Verdict of [product]" or close variants ("Varthaai Verdict script for X", "do the verdict for X"). Combines Ajwad's own Design/Pricing/Taste opinions with the food-quality-analyst skill's ingredients/nutrition analysis into four short verdicts, each with a score out of 10, plus a final aggregate score. Ajwad delivers these live as he films rather than reading a script, so output stays as tight verdict statements, never a flowing narrated script with hooks/b-roll/transitions.
---

# Varthaai Verdict — Reel Talking-Point Generator

The Varthaai Verdict is a blind product review: brand identity is hidden on camera, no
brand is named or shamed, and it ends on a final score. This skill turns raw product
data into the four aspect verdicts + scores + an aggregate score that Ajwad speaks to
live — it does not write flowing narration, hooks, or a shot list.

## Required inputs

Ask for whatever's missing — don't assume:

1. **Product identity** (brand + product name) — used only internally (sheet row,
   confidentiality tracking). Never appears in the spoken output; the reel keeps the
   brand hidden.
2. **Design**: Ajwad's opinion/notes on packaging + a score /10.
3. **Pricing**: price point + Ajwad's opinion/notes + a score /10.
4. **Taste**: Ajwad's opinion/notes + a score /10.
5. **Ingredients list** (as printed) + **nutrition facts panel** (as printed) — this feeds
   the food-quality-analyst skill; also pass along product category and market (default
   India) since that skill needs them.

If ingredients/nutrition analysis for this product was already run earlier in the
conversation, reuse that output rather than re-running it.

## Workflow

### Step 1 — Run the ingredients/nutrition analysis

Invoke the **food-quality-analyst** skill on the ingredients + nutrition facts. Take its
output: legitimacy status, 3–4 verdicts, category score /10, and sources.

### Step 2 — Condense to one camera-ready Ingredients & Nutrition verdict

The food-quality-analyst skill produces 3–4 separate verdicts (that's right for the
public-tool/JSON use case). For the reel itself, condense those into a **single 2–3
sentence verdict** that captures the headline finding(s) — don't just concatenate the
sub-verdicts. If the legitimacy check came back "Significant concern," fold a brief
mention of that into this verdict rather than dropping it. If any sub-verdict was
flagged `controversial: true`, keep it in the condensed verdict rather than quietly
smoothing it out — flag it to Ajwad in your response so he can decide whether to include
it on camera, per his standing instruction that he makes that call, not the script.

### Step 3 — Turn Design/Pricing/Taste notes into verdicts

For each of the three human-scored aspects, tighten Ajwad's raw notes into a **2–3
sentence verdict** matching the tone/format of the ingredients verdict. Rules:

- Don't invent an opinion, detail, or reasoning he didn't give you. If his notes are
  thin (e.g. just "packaging feels cheap, 4/10"), the verdict stays thin and honest
  rather than padded with invented specifics.
- If he already gives full sentences, keep them close to as-is — tighten for length and
  consistency of tone, don't rewrite his judgment.
- Keep it evaluative and specific (what's good/bad and why), not marketing copy.

### Step 4 — Aggregate score

`final_score = average of the 4 category scores (Design, Pricing, Taste, Ingredients & Nutrition)`,
rounded to one decimal. Equal weight by default — same as the food-quality-analyst
skill's own default, adjustable only if Ajwad says so explicitly, and state the formula
used so it's auditable.

### Step 5 — Output

Always produce all three:

**A. On-camera talking-point sheet** (what Ajwad actually uses while filming):
```
[PRODUCT — internal only, do not say on camera]

DESIGN — Score: X/10
<2-3 sentence verdict>

PRICING — Score: X/10
<2-3 sentence verdict>

TASTE — Score: X/10
<2-3 sentence verdict>

INGREDIENTS & NUTRITION — Score: X/10
<2-3 sentence condensed verdict>
[Note any controversial/significant-concern flags here for Ajwad's attention]

FINAL SCORE: X.X/10
```

**B. Sheet-row line** (for pasting into the Google Sheet — one row, pipe-delimited):
```
Product | Design score | Design verdict | Pricing score | Pricing verdict | Taste score | Taste verdict | Ingredients score | Ingredients verdict | Legitimacy status | Final score
```

**C. JSON block** (extends the food-quality-analyst JSON with the human-scored aspects):
```json
{
  "product": "",
  "market": "",
  "design": {"verdict": "", "score": 0},
  "pricing": {"verdict": "", "score": 0},
  "taste": {"verdict": "", "score": 0},
  "ingredients_nutrition": {
    "condensed_verdict": "",
    "score": 0,
    "legitimacy_status": "",
    "controversial": false,
    "full_analysis": "<food-quality-analyst JSON output>"
  },
  "final_score": 0.0,
  "scoring_method": "equal-weight average of 4 category scores"
}
```

## What this skill explicitly does NOT do

- Does not write a narrated voiceover script, hook, cold-open, or b-roll/shot list —
  that's a different format from Varthaai Varthaanm; Ajwad delivers these verdicts live.
- Does not score Design, Pricing, or Taste itself — those scores and opinions come from
  Ajwad; this skill only tightens phrasing.
- Does not soften a controversial or unflattering finding — it surfaces it clearly so
  Ajwad can decide whether to air it, per his standing instruction.
- Does not name or imply the brand anywhere in the spoken verdict text.
