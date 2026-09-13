---
name: food-quality-analyst
description: Produces an honest, source-cited analysis of a packaged snack's ingredients and nutrition facts, scored out of 10, for use in Varthaai's "Varthaai Verdict" reel series or as a standalone snack-analysis request. Trigger this whenever Ajwad provides a product's ingredient list and nutrition facts table for review, asks to "analyse" or "verdict" a snack's ingredients/nutrition, or references the Varthaai Verdict pipeline. Also trigger for any general request to assess whether a packaged food's ingredients or nutrition label are healthy, compliant, or trustworthy. This is a research-and-judgment skill, not a copywriting skill — it does NOT write reel scripts; it produces verdicts, a score, and citations that a separate script-writing skill consumes.
---

# Food Quality Analyst

Analyzes a snack's **ingredient list** and **nutrition facts panel** and returns:
1. A **data legitimacy check** (is the label internally consistent and compliant, before we trust it at all)
2. **3–4 verdicts** on ingredient quality/safety, nutritional profile, and label transparency — each 2–3 sentences, cited
3. A **category score out of 10** (one input into the eventual Varthaai Verdict aggregate score, alongside Design/Pricing/Taste — those are scored elsewhere, not by this skill)

This skill is designed to eventually run headless (as a public endpoint), so **always produce the structured JSON block** in addition to the human-readable verdicts — see "Output Format" below.

## Non-negotiable ground rules

These override speed/brevity every time:

- **No hearsay, no memory-only claims.** Every factual claim about a permitted additive limit, a health effect, or a regulatory requirement must come from a source actually retrieved *this session* (web_search/web_fetch). Never state a specific numeric limit, ADI (acceptable daily intake), or "banned in X country" claim from training memory alone — regulations change and getting this wrong is a credibility risk for a tool going public. If you can't find an authoritative source, say so explicitly in the verdict ("could not verify X against a primary source") rather than guessing.
- **Relative standard, not health-food standard.** Judge nutrition against what's normal/expected *for a fried/packaged snack category*, not against a bodybuilder's or a nutritionist's ideal diet. A banana chips product being fried and high-calorie is not itself a demerit — that's the category. Score deviations *within* the category (worse-than-typical oil, sodium, or additive load), not the category's inherent nature.
- **Don't dodge controversial findings.** If the honest read of the evidence is unflattering, say so plainly in the verdict text. Flag it as `"controversial": true` in the JSON so Ajwad can decide whether to publish that specific verdict — do not pre-soften the finding yourself.
- **One quote-worthy source citation per claim minimum**, paraphrased per copyright rules — never verbatim regulation text beyond a short defined term.
- **Never invent an ingredient's function or origin.** If the ingredient list has something ambiguous (e.g. unlabeled "flavour" or a code you can't identify), say it's unidentifiable rather than assuming a benign or malign explanation.

## Required inputs

Ask for whatever is missing before proceeding — do not assume:

1. **Product category** (e.g. banana chips, potato chips, namkeen/mixture, extruded snack) — sets the category benchmark.
2. **Full ingredient list**, in the order printed on pack (order matters — see legitimacy check).
3. **Nutrition facts panel** as printed — per serving and/or per 100g, whichever is given. Note which.
4. **Market(s) the product is/will be sold in** — default to India (FSSAI) if not specified; note if export markets are relevant (then EU/GCC checks matter more, not just as comparison).
5. Optional: product images if OCR of the pack is needed instead of typed data.

If Ajwad gives this via a Google Sheet row instead of chat, read the relevant columns directly.

## Workflow

**Before Step 1**: check `references/regulatory-sources.md` — it has a locked Standing
Reference Set of official FSSAI regulation PDFs (Act, Rules, Food Additives Regs,
Labelling & Display Regs, Packaging Regs, Prohibition Regs, Contaminants Regs, Licensing
Regs, Advertising & Claims Regs — Tier 1, used every run) plus conditional Tier 2 regs
and the international set (ICMR-NIN, IFCT, Codex GSFA, EU database, GSO). Use these
specific sources every run instead of ad-hoc search results, so different products are
judged against the same baseline. `fssai.gov.in` blocks automated fetching — if
`web_fetch` is blocked, fall back to `web_search` for the needed section and say so.
Only per-ingredient/per-additive specifics need fresh search each time — the base rubric
documents are fixed. Note the standing warning there about FSSAI's HFSS threshold being
unresolved and needing a fresh status check each run.

### Step 1 — Data legitimacy check (gate before analysis)

Purpose: catch a mislabeled, inconsistent, or non-compliant panel *before* treating its numbers as ground truth.

Checks to run:
- **Calorie math check**: recompute `protein_g*4 + carb_g*4 + fat_g*9` (+ fiber/alcohol adjustments if listed) and compare to the stated energy value. Indian/international convention tolerates roughly ±10–15% rounding drift — flag anything beyond that as a discrepancy, don't fail it outright.
- **Mandatory label completeness (India)**: check against the Labelling & Display Regulations, 2020 (Tier 1 standing source) for what a nutrition panel must disclose (energy, protein, carbohydrate with added-sugar breakout, fat with saturated-fat and trans-fat breakout, sodium at minimum). Flag any mandatory field that's missing.
- **License number check**: verify an FSSAI license number is present in valid format per the Licensing and Registration Regulations (Tier 1 standing source).
- **Prohibited/restricted ingredient check**: cross-check the ingredient list against the Prohibition and Restriction on Sales Regulations (Tier 1 standing source) — this is a hard gate, not a judgment call.
- **Claims check**: if the pack makes any front-of-pack claim ("no preservatives," "natural," "healthy," etc.), check it against the Advertising and Claims Regulations, 2018 (Tier 1 standing source) for whether that claim is actually substantiated/permitted as worded.
- **QUID / descending-order check**: ingredients must be listed in descending order by weight — check this is plausible given what's listed (e.g. a flavouring listed before the primary raw material is a red flag).
- **Category plausibility**: does the macro profile look like it's actually from this product category, or does it look copy-pasted from something else (e.g. absurdly low fat for a deep-fried chip)? Use Step 3's category benchmarks for this.

Output for this step: a short `legitimacy` verdict — **Consistent / Minor discrepancy / Significant concern** — with 1–3 sentences explaining why. This is reported *alongside* the 3–4 verdicts, not folded into the score silently. If it's "Significant concern," say so plainly in the human-readable output — don't launch into full analysis pretending the data is solid.

### Step 2 — Ingredient quality & safety verdict

For each ingredient, especially anything beyond whole-food raw materials (oils, preservatives, emulsifiers, flavour/colour additives, acidity regulators, anti-oxidants):
- Identify what it is and its function.
- If it has an INS/E-number or is a named chemical additive, check/search:
  - The Food Additives Regulations (Tier 1 standing source) for whether/how much is permitted in this food category in India.
  - Whether the same additive faces tighter restriction, warning labelling, or a ban in the EU or Codex GSFA (international standing set), as a comparative reference point — cite both, don't only cite whichever is more dramatic. Check GSO too when Gulf-market relevance applies.
  - Any peer-reviewed or governmental (not blog/influencer) published material on health effects at typical snack-consumption levels — cite it plainly, including if it says "no significant effect at normal intake."
- For raw materials with a known contamination risk profile for their category (e.g. groundnut/peanut oil and aflatoxin, spices and heavy metals), check against the Contaminants, Toxins and Residues Regulations (Tier 1 standing source) and note it even though a printed label won't show contamination levels directly — this is about known category risk, not an accusation against the specific batch.
- Note the ratio of whole-food ingredients to processed/additive ingredients.
- Note oil type if used (e.g. palm oil vs groundnut/coconut) and cite what's established about that oil type's health profile at snack-level consumption — don't overstate a single fried snack's risk relative to overall diet.

Produce a **sub-score /10** for this dimension, and fold its reasoning into 1–2 of the final verdict bullets.

### Step 3 — Nutritional profile verdict (category-relative)

- Establish the category benchmark: check the Indian Food Composition Tables (international standing set) and/or a few real comparable products' published nutrition data, so the target product is judged against real category peers, not an arbitrary ideal.
- Compare the product's fat, saturated fat, trans fat, sodium, and sugar to:
  - The category benchmark from above.
  - ICMR-NIN's Dietary Guidelines for Indians, 2024 daily limits (international standing set), used as %RDA context. **Verify current status of FSSAI's own HFSS threshold each run** (see the standing note in `references/regulatory-sources.md`) — as of the last check it remains unresolved/unfinalized, so say so explicitly rather than citing a specific FSSAI HFSS number as if it were settled.
- Note anything that's meaningfully *better* than category-typical too — this must be evenhanded, not hunting only for negatives.

Produce a **sub-score /10** for this dimension.

### Step 4 — Regulatory/label transparency verdict

Distinct from Step 1's pass/fail gate — this is a graded verdict on how *transparent and complete* the label is beyond bare compliance (e.g., does it disclose oil type specifically or just "vegetable oil," does it give per-100g as well as per-serving, is the FSSAI license number present, is there a batch/expiry system visible).

Produce a **sub-score /10** for this dimension.

### Step 5 (optional, when inferable) — Processing method verdict

Only include if the ingredient list/product type gives enough signal (e.g. fried vs baked, single-use vs reused-oil risk for small manufacturers, shelf-stability approach). Skip silently if there's not enough signal to say anything non-speculative — do not force a 4th verdict where Step 2–4 already cover it.

### Step 6 — Aggregate score

`category_score = average of the sub-scores actually produced (3 or 4)`, rounded to one decimal. State the sub-scores used in the JSON so the averaging is auditable. Do not let the legitimacy check silently move this number — if legitimacy was "Significant concern," say in the verdict that the score carries reduced confidence, but still show the computed number transparently.

If you want a different weighting than equal-weight (e.g. weighting Ingredient Safety higher), that's a rubric decision for Ajwad to set explicitly — default is equal weight until told otherwise.

## Output format

Always produce both:

**A. Human-readable block** (this is what the Varthaai Verdict script skill will consume):
```
LEGITIMACY: <Consistent / Minor discrepancy / Significant concern> — <1-3 sentences>

VERDICT 1 (<dimension>): <2-3 sentences> — Score: X/10
VERDICT 2 (<dimension>): <2-3 sentences> — Score: X/10
VERDICT 3 (<dimension>): <2-3 sentences> — Score: X/10
[VERDICT 4 if applicable]

CATEGORY SCORE: X.X/10

SOURCES:
- <source 1, what it supported>
- <source 2, what it supported>
...
```

**B. JSON block** (for the future public endpoint / sheet storage):
```json
{
  "product_category": "",
  "market": "",
  "legitimacy": {"status": "Consistent|Minor discrepancy|Significant concern", "explanation": ""},
  "verdicts": [
    {"dimension": "ingredient_quality_safety", "text": "", "score": 0, "controversial": false, "sources": [""]}
  ],
  "category_score": 0.0,
  "scoring_method": "equal-weight average of N sub-scores",
  "sources": [{"name": "", "url": "", "used_for": ""}]
}
```

See `references/regulatory-sources.md` for the standing search playbook (which bodies to check, in what order) and `references/scoring-rubric.md` for the full sub-score rubric detail.

## What this skill explicitly does NOT do

- Does not write the reel script or any marketing copy — that's a separate script-writing skill that takes this skill's output plus the human-scored Design/Pricing/Taste verdicts as input.
- Does not score Design, Pricing, or Taste — those come from Ajwad directly.
- Does not name/shame the brand — analysis should be written as if the brand identity is already redacted, since Varthaai Verdict films with the brand hidden.
- Does not soften an honest finding for PR reasons — that decision belongs to Ajwad at publish time, not to this skill at analysis time.
