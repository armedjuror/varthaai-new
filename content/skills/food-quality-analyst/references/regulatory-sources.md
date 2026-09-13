# Regulatory & Evidence Search Playbook

## Standing Reference Set — India (official FSSAI texts, Ajwad-supplied, use these exact
## URLs every run — consistency matters)

`fssai.gov.in` blocks automated fetching (robots.txt disallows it). Try `web_fetch` on
these first each run; if blocked, fall back to `web_search` for the specific
section/clause needed and cite the same official document by name/URL as the primary
source, noting plainly that the content came via a search snippet or a secondary summary
of it rather than a direct read, when that's what happened.

**Tier 1 — used on every single product analysis:**

| Regulation | Used for | URL |
|---|---|---|
| Food Safety and Standards Act, 2006 | Base legal framework, definitions | https://fssai.gov.in/docs/food-law/act-2006/Food_Safety_and_Standards_Act_2006.pdf |
| FSS Rules, 2011 | Procedural base rules under the Act | https://fssai.gov.in/docs/food-law/rules-2011/FSS_Gazete_Rules_2011.pdf |
| Food Additives Regulations (FSS Food Products Standards and Food Additives Regs, 2011) | Which additives are permitted in which food category, at what max level — the core additive-safety lookup | https://fssai.gov.in/docs/food-law/regulations/Food_Additives_Regulations.pdf |
| Labelling & Display Regulations, 2020 | Mandatory nutrition panel fields — the core legitimacy-gate lookup | https://fssai.gov.in/docs/food-law/regulations/5fd87c6a0f6adGazette_Notification_Labelling_Display_14_12_2020.pdf |
| Packaging Regulations, 2019 | Packaging material/format compliance | https://fssai.gov.in/docs/food-law/regulations/Gazette_Notification_Packaging_03_01_2019.pdf |
| Prohibition and Restriction on Sales Regulations | Banned/restricted ingredients — check every ingredient list against this | https://fssai.gov.in/docs/food-law/regulations/Prohibition_Regulations.pdf |
| Contaminants, Toxins and Residues Regulations | Heavy metal/pesticide/mycotoxin limits — relevant to the legitimacy and safety checks even though a printed label won't show these directly; cite when raw-material contamination risk is a known issue for the category (e.g. groundnut oil and aflatoxin) | https://fssai.gov.in/docs/food-law/regulations/Contaminants_Regulations.pdf |
| Licensing and Registration Regulations | Whether an FSSAI license number is present/valid-format on the label — part of the transparency verdict | https://fssai.gov.in/docs/food-law/regulations/Licensing_Regulations.pdf |
| Advertising and Claims Regulations, 2018 | Checks any front-of-pack claim ("no preservatives," "natural," "healthy," etc.) against what's actually permitted to be claimed — part of the transparency/legitimacy verdict | https://fssai.gov.in/docs/food-law/regulations/Gazette_Notification_Advertising_Claims_27_11_2018.pdf |

**Tier 2 — conditional, only when the product/claim triggers it:**

| Regulation | Trigger |
|---|---|
| Food Fortification Regulations | Product claims fortification (e.g. fortified oil, iodized salt) |
| Organic Food Regulations (2017) | Product claims "organic" |
| Non-Specified Food Ingredients notification | An ingredient isn't covered by any standard food category — check this before flagging it as unidentifiable |
| Health Supplements/Nutraceuticals Regulations | Product makes a functional/health-supplement claim (unlikely for a standard snack, but check if e.g. protein content is marketed as a benefit) |
| Alcoholic Beverages notification | Not applicable to snack foods — listed here only to rule it out |

**Tier 3 — background only, rarely cited directly in a verdict:**

- Laboratory and Sample Analysis Regulations (testing methodology — procedural, not a
  content standard)
- Food Safety Auditing notification (audits FBOs, not individual products)

## International standing set (used per-additive/per-nutrient, not fixed to one document)

| Source | Used for | URL |
|---|---|---|
| ICMR-NIN Dietary Guidelines for Indians, 2024 | Daily limits for sugar/salt/saturated fat — India's own working reference point, see HFSS note below | https://nin.res.in/dietaryguidelines/pdfjs/locale/DGI_2024.pdf |
| Indian Food Composition Tables (IFCT) 2017, NIN-ICMR | Category benchmarking — what's normal for a given snack/ingredient type | https://www.nin.res.in/ebooks/IFCT2017.pdf |
| Codex GSFA Online | International baseline additive limit when FSSAI's own limit is unclear/unset | https://www.fao.org/gsfaonline |
| EU Food Additives Database (Regulation (EC) No 1333/2008) | Comparative check: does the EU restrict/ban an additive India permits more freely | https://food.ec.europa.eu/safety/food-improvement-agents/additives/database_en |
| GSO 2500 (Additives Permitted for Use in Food Stuffs) & GSO 2233 (Nutritional Labelling) | Gulf-market comparison, relevant given Varthaai's stated Gulf interest | https://www.gso.org.sa (store — often paywalled; see note below) |

**Important standing note on FSSAI's HFSS threshold (verify current status each run —
this is actively moving):** as of an August 3, 2026 Supreme Court affidavit, FSSAI has
**not** finalized a High Fat/Sugar/Salt threshold or warning-label system — it withdrew
its 2022 draft and is instead proposing a nutrition table showing %RDA contribution
(using ICMR-NIN's Dietary Guidelines as the reference), not front-of-pack warning
symbols. Do not assert a specific FSSAI HFSS numeric threshold exists — there isn't a
finalized one. Use the ICMR-NIN daily limits as the working relative-benchmark in the
meantime, since that's the same reference FSSAI itself is now pointing to, and say
explicitly that FSSAI's own threshold is still unresolved. Re-check this status each
run — it's the single fastest-moving fact in this whole rubric.

**GSO note:** GSO standards are frequently only available for purchase through the GSO
store. If the primary text isn't fetchable, cite a reputable secondary source (trade-
compliance firm summary, WTO TBT notification) and say plainly it's secondary, not
primary — don't present a paraphrase of a paywalled document as if it were read directly.

## Search order and what each body is used for beyond the standing set

The standing set above covers the base rubric. Per-ingredient/per-additive specific
figures still need live search each time within these documents (there's no way to
pre-bake limits for every possible additive/food category combination) — do not rely on
memorized numeric limits, since these are exactly the kind of fact that changes and
where being wrong damages credibility.

## 1. India — FSSAI additive-specific lookups

- Search pattern: `FSSAI [additive name OR INS number] permitted limit [food category]`,
  or go directly into the Food Additives Regulations PDF above.
- If fssai.gov.in isn't fetchable, cross-check via reputable secondary coverage but say so.

## 2. Codex Alimentarius — additive-specific lookups

- Search pattern: `Codex GSFA [additive name] maximum use level`, or search directly at
  the standing-set URL above.

## 3. EU — EFSA / Regulation (EC) No 1333/2008 — additive-specific lookups

- Never treat "EU allows it" or "EU restricts it" alone as a verdict on safety — cite the
  actual EFSA opinion or rationale where findable.
- Search pattern: `EFSA opinion [additive name]`, `[additive name] E-number EU restriction`.

## 4. Gulf / GCC — GSO — additive-specific lookups

- Search pattern: `GSO [additive name] GCC food standard`, `Gulf countries food additive
  regulation [name]`.

## 5. Peer-reviewed / governmental health evidence

- For health-effect claims (not just legal-limit claims): prefer PubMed-indexed studies,
  WHO/ICMR-NIN (Indian Council of Medical Research – National Institute of Nutrition)
  guidance, or systematic reviews over single studies, blogs, or wellness-influencer
  content.
- Explicitly note the strength of evidence: a single small study vs an established
  regulatory consensus are not equal weight — say which one you're citing.
- Search pattern: `[ingredient/additive] health effects systematic review`,
  `ICMR NIN dietary guidelines [nutrient]`.

## 6. Category benchmarking (for Step 3 of SKILL.md)

- Pull typical nutrition values for the same snack category from a few real products'
  published labels or from published food-composition tables (e.g. IFCT — Indian Food
  Composition Tables), not from a single competitor cherry-picked to make the subject
  look good or bad.
- Search pattern: `[snack category] nutrition facts per 100g`, `Indian Food Composition
  Table [ingredient]`.

## Citation hygiene

- Every source used goes in the `sources` array in the JSON output with what it was
  used to support.
- Paraphrase regulation/study content per copyright rules — never reproduce more than a
  short defined term or figure verbatim.
- If a claim can't be sourced after a reasonable search, write "could not verify against
  a primary source" in the verdict text rather than dropping the caveat silently.
