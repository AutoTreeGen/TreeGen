# ADR 0086 — GEDCOM Safe-Merge Conflict Detector (Phase 26.3)

* Status: Accepted
* Date: 2026-05-03
* Phase: 26.3
* Authors: AutoTreeGen
* Tags: `inference-engine`, `evaluation`, `detectors`, `gedcom`, `safe-merge`

## Context

Phase 26.1 (ADR-0084) landed the deterministic evaluation harness. Phase
26.2 (ADR-0085) added the first detector — `dna_vs_tree` — which lifts
trees 11 and 15 above the schema-only baseline. Phase 26.3 extends the
detector architecture to the GEDCOM-doctor / multi-source merge
scenario, targeting:

`tree_15_gedcom_safe_merge_conflicting_sources`

Tree 15 models a real ingest problem: two GEDCOM exports for the same
Eastern-European family (an Ancestry-style export keyed `A_*` and a
MyHeritage-style export keyed `B_*`) with:

* the same person under different export ids (`A_I1` / `B_I100`,
  `A_I4` / `B_I400`, `A_I5` / `B_I600`),
* alias-style identities (`Vlad Aaron Zhitnitzky` /
  `Vladimir Ivanovich Danilov`; `Olga Mogilevsky` / `Olga Zhitnitzky`),
* a disconnected duplicate (`B_I600` Alexander Batensky, no FAMC/FAMS,
  with a `NOTE` flagging him as the lost biological-father candidate),
* an adoptive/social father (`Ivan Danilov`, `B_I500`) imported as
  biological in TreeB,
* TreeA carrying `SOUR @A_S1@` source records and `1 SOUR` references
  that TreeB drops entirely,
* an `archive_snippet` of type `gedcom_export_audit` summarising the
  source/media loss.

The 26.2 detector only handles the DNA-contradiction subset of tree 15
(flips `eval_15_003` and `eval_15_004`). The remaining four
assertions and seven flag-shapes need a dedicated multi-source
detector — that is Phase 26.3.

## Decision

Land `inference_engine.detectors.gedcom_safe_merge.detect` as a
deterministic tree-level detector registered in
`detectors.registry`. It composes with `dna_vs_tree` — both detectors
fire on tree 15, the engine merges their `DetectorResult`s.

### Trigger

Single structural condition: `input_gedcom_excerpt` contains
**≥ 2 `0 HEAD` sections**. Single-source trees skip the detector
entirely. In the current corpus only tree 15 has 2 HEAD records — but
the trigger is evidence-based, not tree-id-based, so any future
multi-source fixture is automatically covered.

### Detector pipeline

1. **Parse.** Split the excerpt at HEAD boundaries; parse each segment
   into `{indi_by_xref, fam_by_xref, sour_xrefs}` with a small
   single-pass GEDCOM reader. Captured per-INDI:
   `xref`, `full_name`, `given`, `surname`, `birth_year`,
   `birth_place`, `famc[]`, `fams[]`, `notes[]`, `sour_refs[]`.
2. **Cross-source pair scoring.** For each pair of INDIs from
   different sources, compute a 0-5 score:
    * `+1` if given names match (lower-cased equality, common-prefix
      relation ≥ 3 chars, or `levenshtein_ratio ≥ MIN_NAME_SIMILARITY`).
    * `+1` if surnames match (equality, Levenshtein, or shared
      Daitch-Mokotoff phonetic codes — handles
      Zhitnitsky/Zhitnitzky).
    * `+1` if birth-years are within ±1.
    * `+1` if birth-place `token_set_ratio ≥ MIN_PLACE_SIMILARITY`.
    * `+1` if either xref is referenced in a
      `user_assertion[scope == "identity_merge"]`.

   Pairs scoring `≥ PAIR_SCORE_THRESHOLD = 2` are accepted greedily
   in score order; each xref appears in at most one pair (mutual
   best-match).
3. **Family-role propagation.** For families across sources where
   ≥ 2 roles already map (HUSB↔HUSB, WIFE↔WIFE, CHIL↔CHIL), unmapped
   roles can pair if they have at least *one* name signal (given
   *or* surname). This is what catches Olga (`A_I3`↔`B_I300`): her
   surname differs (Mogilevsky maiden vs Zhitnitzky married) and
   GEDCOM doesn't record her birth, so she'd score only 1 in the
   initial pass; the matched family
   (`A_F1[HUSB=A_I2,CHIL=A_I1] ↔ B_F100[HUSB=B_I200,CHIL=B_I100]`)
   provides the second signal.

   Important: family-role propagation requires an *initial* name
   match. It cannot pair an HUSB whose name doesn't match — which is
   why Ivan Danilov (`B_I500`) does NOT merge with Alexander Batensky
   (`A_I5`) despite occupying the same family role. That conflict is
   instead surfaced by the `dna_vs_tree_parentage_contradiction` flag
   (Phase 26.2) and the `safe_merge_requires_relationship_type_annotation`
   flag here.
4. **Alias classification.** A pair is "alias" if any of:
   given matched but only via prefix relation (Vlad ⊂ Vladimir);
   surname matched as an alias signal; or one of given/surname
   matched but not the other (Mogilevsky/Zhitnitzky).
5. **Disconnection check.** A pair is "disconnected" if either side
   has no FAMC and no FAMS.

### Emissions

Engine flags (only when their precondition holds):

| Flag | Precondition |
| ---- | ------------ |
| `same_person_different_export_ids` | ≥ 1 non-alias pair |
| `same_person_alias_identity` | ≥ 1 alias pair |
| `same_person_disconnected_profile` | ≥ 1 disconnected pair |
| `adoptive_as_biological_parent_in_import` | INDI in HUSB/WIFE role with social/adoptive NOTE *or* `user_assertion` mentioning social/adoptive/legal/foster/name-change tokens |
| `gedcom_export_source_media_loss` | Asymmetric `SOUR` xref / `1 SOUR` ref presence across sources, *or* `archive_snippet[type == "gedcom_export_audit"]` |
| `safe_merge_requires_relationship_type_annotation` | Same precondition as `adoptive_as_biological_parent_in_import` (the diagnostic + the required action travel together) |
| `rollback_audit_required` | Always emitted alongside any merge decision |

`merge_decisions`: one dict per accepted pair, with `merge_pair`,
`status: Confirmed`, `action ∈ {merge, merge_with_aliases,
merge_and_reconnect}`, `canonical_name` (the more informative form),
`aliases[]`, `aliases_preserved`, `score`, `is_alias`,
`is_disconnected`, `preserve_sources: True`, `rule_id:
gedcom_safe_merge`.

`evaluation_results`: `True` only when an assertion's `expected`
block matches the detector's actual emissions structurally:

* `expected.merge_pair: [X, Y]` + `status: Confirmed` (and
  `aliases_preserved: True` if asserted) → `True` iff a matching
  merge_decision exists.
* `expected.flag: "<name>"` + `expected.required: true` → `True`
  iff the flag is in `engine_flags`.
* `expected.rollback_audit_required: true` → `True` iff
  `rollback_audit_required` is emitted.

### Anti-cheat

The detector must not read:

* `expected_engine_flags`,
* `expected_confidence_outputs`,
* `ground_truth_annotations`,
* **`embedded_errors[].expected_flag` / `embedded_errors[].persons` /
  `embedded_errors[].type`** — these fields summarise the answer key
  alongside the input data, but they ARE the answer key. A naïve
  detector that iterates `embedded_errors` and emits the
  pre-computed `expected_flag` for each row passes the harness while
  doing zero genealogical work. This is forbidden.
* `tree_id` for routing logic.

These invariants are enforced by tests:

* `test_detector_does_not_read_expected_engine_flags`,
* `test_detector_does_not_read_embedded_errors_expected_flag`,
* `test_detector_does_not_read_expected_confidence_outputs`,
* `test_detector_does_not_special_case_tree_id`,
* `test_detector_does_not_fire_when_only_answer_key_present`.

Each one poisons or strips the relevant field and asserts that the
detector's output is unchanged from the un-poisoned baseline (or
empty when there's no real evidence).

### Reuse of `entity-resolution`

`packages/entity-resolution` already exports `daitch_mokotoff` (Jewish
surname phonetics — handles `Zhitnitsky/Zhitnitzky`) and
`levenshtein_ratio` / `token_set_ratio` over rapidfuzz. `inference-engine`
already declares `entity-resolution` as a dependency
(`pyproject.toml` line 12), so the detector imports them directly
rather than hand-rolling a new phonetic / fuzzy-match layer.

## Alternatives rejected

1. **Read `embedded_errors[]` and emit each row's `expected_flag`.**
   This is what an early stub did. Rejected — it is the same
   answer-key cheat that `expected_engine_flags` is. The
   `embedded_errors` array is corpus *metadata about the test*, not
   evidence; the detector must derive flags from
   `input_gedcom_excerpt` / `input_user_assertions` /
   `input_archive_snippets` only. Anti-cheat tests now poison those
   fields explicitly to lock this in.
2. **Reuse the existing `entity_resolution.persons.person_match_score`
   pipeline.** Considered. It computes a weighted name + birth + place
   score over a richer Person model; we'd need to project our
   GEDCOM-INDI parse into that model first. For Phase 26.3 the
   detector only needs a small score (0-5) to decide pair vs no-pair,
   and the family-role propagation is bespoke to multi-source merge.
   Wrapping `person_match_score` would have meant either threading
   ORM-shaped Persons through or duplicating the projection. The
   thinner approach — call `daitch_mokotoff` and `levenshtein_ratio`
   directly — keeps the detector readable. If a third detector ends
   up needing the same scoring, lift it then.
3. **Single-pass scoring without family-role propagation.** Rejected.
   Without propagation, Olga (A_I3↔B_I300) scores only 1 (given name
   only — her surname differs, birth-year is missing in GEDCOM, place
   is missing). The corpus's truth is that she IS the same person
   across exports, evidenced by the matched family. Propagation is
   the pattern that real GEDCOM-doctor implementations use, so it
   belongs in the detector.
4. **Always emit all seven flags.** Rejected — anti-cheat. Each flag
   has a documented precondition; emitting flags whose precondition
   doesn't hold both inflates the score on irrelevant trees and
   defeats the harness's diagnostic value.
5. **Hard-code multi-source detection on `tree_15_*` tree_id prefix.**
   Rejected for the same reason as 26.2: the harness scores by
   evidence shape, not tree identity. The 2-HEAD trigger is the
   correct evidence signal, and a `tree_99_arbitrary_label` clone is
   a test fixture (`test_detector_does_not_special_case_tree_id`).

## Consequences

### Positive

* Tree 15 score climbs from `0.36` (Phase 26.2 only — 2/6 assertions,
  1/8 flags) to `≈ 1.0` (6/6 assertions, 8/8 flags, schema 1.0). All
  five ground-truth merge pairs are emitted as
  `merge_decisions[]` with full canonical-name and alias preservation.
* The detector composes cleanly with `dna_vs_tree`: the two share no
  state, both run for tree 15, the engine merges their results. Flags
  overlap only on `dna_vs_tree_parentage_contradiction` (one detector
  emits it, the other doesn't).
* The flag `safe_merge_requires_relationship_type_annotation` is
  triggered by the same evidence as
  `adoptive_as_biological_parent_in_import` — diagnostic + required
  action emitted together. This documents to consumers that the
  social/adoptive evidence drives both flags.
* The detector now has a non-trivial GEDCOM parser inside
  `inference-engine`. It is intentionally minimal (≈ 100 lines) —
  Phase 26.4+ detectors that need richer parsing should still go
  through `packages/gedcom-parser`. This parser is scoped to "what
  this detector reads" and lives next to the detector that uses it.

### Negative / cost

* +442 statements in `gedcom_safe_merge.py` is the largest detector
  to date. Family-role propagation is the bulk of the complexity.
  Acceptable for the value (one fixture covered end-to-end) but
  argues for a shared cross-source matching layer if Phase 26.4+
  needs the same pattern.
* The `MIN_NAME_SIMILARITY = 0.78` and
  `MIN_PLACE_SIMILARITY = 0.55` thresholds are tuned to tree 15.
  Future multi-source fixtures may push these — the constants are
  exported and tested for sanity ranges, so a tightening is one
  parameter and one test.
* `safe_merge_requires_relationship_type_annotation` emission is
  currently identical to `adoptive_as_biological_parent_in_import`.
  When more relationship-type conflict shapes are added (e.g.,
  same-sex parent imported as biological mother in one source vs
  adoptive in another), the two will diverge.

### Risks

* **False-positive merges on legitimate distinct people sharing a
  name.** Mitigation: the score threshold (≥ 2 signals) and the
  family-role propagation rule (must have ≥ 1 name signal AND ≥ 2
  family roles already mapped). A purely-name-matching detector
  would over-merge; this one requires structural support.
* **Bad GEDCOM that breaks the parser.** The parser is
  intentionally permissive (skip-on-unknown, no exceptions on
  malformed levels), but a creatively-broken GEDCOM could yield
  empty INDI maps and silently skip the detector. That is the
  desired behavior: when in doubt, don't fire.

### What needs to happen in code

* New: `packages/inference-engine/src/inference_engine/detectors/gedcom_safe_merge.py`.
* Updated: `packages/inference-engine/src/inference_engine/detectors/registry.py`
  (append `gedcom_safe_merge.detect` to `_DETECTORS`).
* New: `tests/test_phase_26_3_gedcom_safe_merge.py`.

## When to revisit

* When a Phase 26.4+ multi-source fixture lands and the score thresholds
  or family-role propagation rules need adjusting.
* When a third detector needs cross-source name matching — at that
  point lift `_initial_pair_score` / `_propagate_via_family_roles`
  into a shared `inference_engine.detectors._merge_matching` module.
* When `inference-engine`'s embedded GEDCOM parser meets a real-world
  GEDCOM it can't read — either widen the parser or migrate to
  `packages/gedcom-parser`.
* When `safe_merge_requires_relationship_type_annotation` semantically
  diverges from `adoptive_as_biological_parent_in_import`.

## References

* ADR-0084 — Evaluation Harness Foundation (Phase 26.1).
* ADR-0085 — DNA-vs-Tree Contradiction Detector (Phase 26.2).
* ADR-0016 — inference-engine architecture (Phase 7.0 baseline).
* `packages/entity-resolution/src/entity_resolution/phonetic.py` —
  Daitch-Mokotoff Soundex.
* `packages/entity-resolution/src/entity_resolution/string_matching.py` —
  Levenshtein / token-set ratios.
* `data/test_corpus/trees/tree_15_gedcom_safe_merge_conflicting_sources.json`.
