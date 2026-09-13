# Scoring Rubric Detail

Default: **equal-weight average** of whichever sub-scores were actually produced (3, or 4
if Step 5 applied). This file exists so the rubric is explicit and auditable, and so
Ajwad can hand-tune weights later without touching SKILL.md's workflow logic.

## Sub-score: Ingredient Quality & Safety (0–10)

- 9–10: Near-entirely whole-food ingredients; any additives present are common,
  well-studied, permitted with wide margin, and unrestricted elsewhere.
- 6–8: Mostly whole-food with a small number of standard, low-concern additives
  (common preservatives/anti-oxidants at typical levels).
- 3–5: Meaningful reliance on processed ingredients/additives, including at least one
  that faces tighter restriction or a warning requirement in another major jurisdiction
  (EU/GCC), even if legal in India.
- 0–2: Additive(s) at or near permitted limits, unidentifiable ingredients, or
  ingredients flagged in credible published health evidence at snack-consumption levels.

## Sub-score: Nutritional Profile, category-relative (0–10)

Scored against the category benchmark (Step 3), not an absolute health ideal.
- 9–10: Notably better than category-typical on fat/saturated fat/sodium/sugar.
- 6–8: In line with category-typical.
- 3–5: Worse than category-typical on one or more dimensions.
- 0–2: Substantially worse than category-typical, or breaches an FSSAI HFSS threshold
  where one exists.

## Sub-score: Regulatory/Label Transparency (0–10)

- 9–10: Fully compliant plus goes beyond minimum disclosure (specific oil named, both
  per-serving and per-100g given, license/batch info clear).
- 6–8: Fully compliant, minimum disclosure only.
- 3–5: Minor omissions or ambiguity (e.g. generic "vegetable oil," missing per-100g).
- 0–2: Missing mandatory fields identified in the Step 1 legitimacy check.

Note: a "Significant concern" legitimacy status should generally cap this sub-score at
5 or below — a label that fails its own compliance gate cannot simultaneously score
as highly transparent.

## Sub-score: Processing Method (0–10, optional)

Only scored when Step 5 applies. No universal ladder here since it's highly product-
dependent — write the reasoning in the verdict text itself and score relative to what's
typical/achievable for that processing method (e.g. among fried snacks, oil choice and
apparent freshness practices; among baked/extruded snacks, comparable considerations).

## Changing the weighting

If Ajwad wants non-equal weights (e.g. Ingredient Safety weighted 40%, Nutrition 30%,
Transparency 20%, Processing 10%), apply that explicitly and state the formula used in
`scoring_method` in the JSON output — never apply a non-default weighting silently.
