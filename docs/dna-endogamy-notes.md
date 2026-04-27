# DNA endogamy notes

> **Audience:** developers extending DNA-related code (rules, parsers,
> hypothesis_runner) and end-users with endogamous ancestry (Ashkenazi
> Jewish, Quebec French, Amish, etc.).
>
> **Status:** Living document. Phase 7.3 baseline; calibration on real
> user data is Phase 8+.

---

## What endogamy is

**Endogamy** — long-running mate selection inside a closed population:
limited by religion, geography, language, caste, or persecution.
Generations of unions inside a small founding pool (founder effect)
mean that two random members of the population share more recent
common ancestors than two random members of an outbred population
of the same census size.

Practical examples relevant to AutoTreeGen users:

- **Ashkenazi Jewish (AJ)** — small founder population (estimated
  ~350–400 effective founders ~600–800 years ago), strict religious
  endogamy until ~1900. Modern AJ individuals share substantial
  background DNA even when their nearest documented common ancestor
  is 8+ generations back.
- **Sephardi Jewish (SJ)** — similar mechanism with different
  geographic origin (Iberian peninsula, then diaspora). Less extreme
  than AJ but still present.
- **Quebec French (Canadiens)** — rapid expansion from a few thousand
  17th-century founders, isolated by language and religion.
- **Amish / Mennonite** — strict endogamy + small founding population
  → among the highest documented endogamy in North America.
- **Hutterite, LDS Pioneer descendants** — comparable mechanisms.

Less extreme but still present: most rural / island / persecuted-minority
populations.

---

## Why endogamy inflates shared cM

DNA matching algorithms (ours, Ancestry, MyHeritage, FTDNA) measure
**total shared centiMorgans (cM)** between two kits and report it as a
proxy for relationship distance. The Shared cM Project 4.0
([Bettinger / DNA Painter](https://dnapainter.com/tools/sharedcmv4))
gives reference distributions: e.g. true 1st cousins share 396–1397 cM
(median ≈ 866).

That distribution is calibrated on **outbred European-American
samples** (the Project's largest cohort). In an endogamous cohort, two
people who are documented 1st cousins also carry **additional small
shared segments** from the deeper population background. Those
extra segments add to total cM without representing the documented
relationship.

Concrete impact:

- Two AJ individuals who are documented 4th cousins ≈ general
  population 4th cousins (~35 cM mean) **plus** ~30–80 cM background
  → observed total can sit around 50–120 cM, which the unadjusted
  Shared cM Project reads as **2nd–3rd cousin** range.
- The same effect on close relationships is smaller: a true AJ
  parent-child still sits in the 2376–3720 cM band — extra background
  is dwarfed by the genuine 50% sharing.

So the rule of thumb is: **endogamy inflates more, the more distant
the true relationship**. Close relationships (parent-child, full
siblings) are reasonably robust; 3rd–6th cousins drift heavily.

---

## How AutoTreeGen handles it (Phase 7.3 baseline)

Three concrete pieces of plumbing:

1. **`DnaKit.ethnicity_population`** column
   (`packages/shared-models/src/shared_models/orm/dna_kit.py`) —
   String-enum field on every kit. Default: `general`.
2. **`EthnicityPopulation`** enum
   (`packages/shared-models/src/shared_models/enums.py`):

   | Value           | Multiplier (Bettinger baseline) |
   |-----------------|---------------------------------|
   | `general`       | 1.0                             |
   | `ashkenazi`     | ≈ 1.6                           |
   | `sephardi`      | ≈ 1.4                           |
   | `amish`         | ≈ 2.0                           |
   | `lds_pioneer`   | ≈ 1.5                           |

3. **`DnaSegmentRelationshipRule`**
   (`packages/inference-engine/src/inference_engine/rules/dna.py`) —
   reads both kits' ethnicity, takes `max(multiplier_a, multiplier_b)`
   (most conservative), and divides the rule's emitted weight by that
   multiplier. Direction (SUPPORTS / CONTRADICTS) is **not**
   re-classified — only confidence is reduced.

Example for AJ pair, parent_child hypothesis, total = 2700 cM:

- Base weight (`parent_child` SUPPORTS): **0.80**
- Multiplier (`max(1.6, 1.6)`): **1.6**
- Adjusted weight: **0.50**
- Direction: **SUPPORTS** (unchanged — DNA in parent-child range).

The adjustment lands in `Evidence.source_provenance.endogamy_multiplier`
so the audit-trail / hypothesis review UI can show *why* confidence
was reduced.

---

## How the multiplier gets set

For Phase 7.3 — **manual flag**. The kit owner sets
`DnaKit.ethnicity_population` either:

- via UI (Phase 6.x DNA dashboard, when shipped),
- via API on kit creation (e.g. `POST /dna/kits` payload),
- via direct SQL during ad-hoc maintenance.

Auto-detection (e.g. surname-based AJ classifier, or PCA-derived
population coordinates from the kit's own SNP profile) is **deferred**
to a later phase. The reasons:

- Surname-based classifiers leak unintended assumptions (a Cohen
  surname in 2026 is not necessarily AJ; a non-AJ surname in an AJ
  paternal line, e.g. after 19th-century Russian-empire forced
  surname assignment, breaks the classifier).
- PCA-from-SNP needs reference panels (1000 Genomes / HGDP), which
  imports licensing and binary-blob complexity we don't want yet.

For the owner of the project (AJ ancestry): the default kits will
be flagged `ashkenazi` manually at upload time. Anyone with mixed
ancestry should pick the more restrictive label (e.g. AJ + general
mixed → `ashkenazi`) — rule takes max so the choice is conservative.

---

## What the multiplier is NOT

- It is **not a posterior probability adjustment** in the Bayesian
  sense. We have no prior distribution over relationships in the
  current tree-context. Phase 7.4+ tree-prior + ADR-0023
  «Когда пересмотреть» — we may switch to a posterior-style formula
  there.
- It is **not population-genetic IBS-vs-IBD correction**. A real
  fix needs phasing + segment-level identity-by-descent. Phase 6.4
  (phasing + IBD2) is the right path; we'll integrate it into rules
  when it lands.
- It is **not cohort-specific** (AJ-Lithuanian vs AJ-Polish vs
  AJ-Hungarian show different multipliers in fine-grained studies).
  The single AJ multiplier is a baseline; refining requires
  user-data calibration (Phase 8+).
- It is **not a per-generation correction.** Reality is that the
  multiplier varies with relationship distance — the baseline values
  are a single number that matches mid-range cousins reasonably well
  and underadjusts very distant matches. A distance-aware multiplier
  is a Phase 8+ improvement.

---

## Reference data attribution

- [Shared cM Project 4.0 — DNA Painter](https://dnapainter.com/tools/sharedcmv4)
  — primary cM range table (CC-BY 4.0). Used in
  `packages/dna-analysis/src/dna_analysis/matching/relationships.py`
  and (via thresholds) in
  `packages/inference-engine/src/inference_engine/rules/dna.py`.
- [Endogamy and DNA — Genetic Genealogist](https://thegeneticgenealogist.com/2017/08/26/endogamy-and-dna/)
  — Bettinger's blog post explaining the inflation effect with
  worked examples.
- [Shared cM Project endogamy variant](https://dnapainter.com/tools/sharedcmv4/about)
  — DNA Painter discusses the AJ adjustment and provides the
  multiplier ranges this codebase uses.

---

## Related documents

- `docs/adr/0014-dna-matching-algorithm.md` — DNA matching algorithm,
  Shared cM Project 4.0 source, noise-floor 7 cM.
- `docs/adr/0023-dna-aware-inference.md` — formal contract for
  `DnaSegmentRelationshipRule` and the endogamy adjustment design.
- `docs/runbooks/dna-matching-usage.md` — user guide for running the
  pairwise DNA match CLI (Phase 6.1).
- `docs/runbooks/dna-data-handling.md` — privacy + storage runbook.
- ROADMAP §10.2.4 — endogamy adjustment as a roadmap item.
- CLAUDE.md §3.7 — domain-aware (Eastern European / Jewish genealogy).
