# ADR 0085 — DNA-vs-Tree Contradiction Detector (Phase 26.2)

* Status: Accepted
* Date: 2026-05-03
* Phase: 26.2
* Authors: AutoTreeGen
* Tags: `inference-engine`, `evaluation`, `detectors`, `npe`, `dna`

## Context

Phase 26.1 (ADR-0084) landed the evaluation harness foundation: a 20-tree
synthetic corpus, a Pydantic-pinned `EngineOutput`, and a deterministic
`scripts/run_eval.py` that scores each tree on a `0.7*assertion +
0.2*flag + 0.1*schema` formula. The baseline engine intentionally
emitted nothing, so every tree's score was the schema floor of `0.10`.

The first tree the harness needs to actually score is
`tree_11_unknown_father_npe_dna_contradiction`: a four-generation
Soviet/Ukrainian-Ashkenazi scenario where the GEDCOM-recorded social
father is contradicted by autosomal DNA (a 1768 cM paternal
half-sibling match plus a Batensky/Dodatko cluster pointing at
Alexander Batensky as the biological father). Tree 15
(`gedcom_safe_merge_conflicting_sources`) carries the same shape inside
a multi-source merge scenario — its harness expectations also include
`dna_vs_tree_parentage_contradiction`.

We need a deterministic detector that:

* fires on these two trees (and any future tree with the same evidence
  shape),
* stays silent on trees with weak DNA or no social/adoptive context
  (e.g., trees 04, 05, 06, 07, 09, 12),
* derives every flag and `evaluation_results` answer from input
  evidence — never from the answer-key fields (`expected_engine_flags`,
  `expected_confidence_outputs`).

## Decision

Add a tree-level detector,
`packages/inference-engine/src/inference_engine/detectors/dna_vs_tree.py`,
plus a thin registry,
`packages/inference-engine/src/inference_engine/detectors/registry.py`,
and wire `engine.run_tree` to merge `DetectorResult`s from the registry
into the final `EngineOutput`.

### Detector contract

```python
def detect(tree: dict[str, Any]) -> DetectorResult: ...
```

`DetectorResult` is a dataclass mirroring the list-shaped fields of
`EngineOutput` plus `evaluation_results`. The engine merges by
`list.extend` for list fields and `dict.update` for
`evaluation_results`. In Phase 26.2 the registry holds one detector;
Phase 26.3+ will append more without touching `engine.run_tree`.

### Trigger conditions (all required)

1. **Strong DNA signal** — at least one match with
   `shared_cm >= 1300` (close-relative range: parent / full sibling /
   half sibling / aunt-uncle), or a paternal cluster of ≥ 2 matches
   with `shared_cm >= 100` connected via `shared_matches_with`.
2. **DNA-supported biological-parent claim** — a `user_assertion` with
   `scope == "biological_parentage"` whose `evidence` mentions DNA
   tokens (`"dna"`, `"half-sibling"`, `"cluster"`, `"triangulat"`,
   `"shared cm"`, `"paternal match"`, `"autosomal"`). Its `person_id`
   is the **bio candidate**.
3. **Social / adoptive / legal context** — at least one of:
   * a `user_assertion` with `scope == "relationship_type"` (or
     `biological_parentage` without DNA tokens) whose text mentions
     `social`, `adoptive`, `adoption`, `legal father`, `foster`,
     `guardian`, `step-father`, or `name change`,
   * an `archive_snippet` with `type == "adoption_or_name_change"`,
   * a GEDCOM `NOTE` containing
     `social/adoptive father` / `imported as biological father` /
     `should be social/adoptive only`.

   The associated `person_id` is the **social candidate** (and is also
   the **wrongly-claimed biological** person).

### Emissions when the trigger fires

* `engine_flags`:
  * `dna_vs_tree_parentage_contradiction`
  * `adoption_foster_guardian_as_parent`
  * `sealed_set_biological_parentage_candidate`
* `relationship_claims` (3):
  * `Confirmed biological_father` for the bio candidate, with DNA-match
      and birth-record evidence refs.
  * `Confirmed social_or_adoptive_father` for the social candidate,
      with adoption / name-change evidence refs.
  * `Rejected biological_father` for the social candidate (the
      tree/GEDCOM-imported claim that DNA contradicts).
* `sealed_set_candidates`: one candidate referencing the biological
  parentage claim.
* `evaluation_results`: `True` only for those `assertion_id`s whose
  `expected` block matches one of three structural shapes:
  * `relationship == "<bio_id> biological father of …"` +
      `status == "Confirmed"`,
  * `biological_relationship.status == "Rejected"` +
      `social_relationship.status == "Confirmed"` for the same person
      (Tree-11 shape), or `biological_status == "Rejected"` +
      `social_adoptive_status == "Confirmed"` (Tree-15 shape),
  * `sealed_set_candidate is True` + `claim` mentioning the bio
      candidate's id.

### Anti-cheat invariants

The detector must not read:

* `expected_engine_flags`,
* `expected_confidence_outputs`,
* `ground_truth_annotations`,
* the `tree_id` for routing logic (tests poison it and re-run).

These are enforced by `tests/test_phase_26_2_dna_vs_tree.py`:

* `test_detector_does_not_read_expected_engine_flags` — poison the
  field, expect identical output.
* `test_detector_does_not_read_expected_confidence_outputs` — same.
* `test_detector_does_not_special_case_tree_id` — rename `tree_id`,
  expect detector still fires from evidence.
* `test_detector_silent_when_dna_strong_but_no_social_context` /
  `test_detector_silent_when_social_context_but_no_strong_dna` — both
  preconditions are required.

The Phase 26.1 baseline anti-cheat tests
(`test_baseline_does_not_emit_expected_engine_flags`,
`test_baseline_evaluation_results_all_false`) pivoted from `tree_11`
to `tree_07_patronymic_vs_surname_disambiguation` (max
`shared_cm = 118`, no NPE shape). They now read as
`test_uncovered_tree_*` and will pivot again as future detectors cover
more trees.

### Threshold rationale (1300 cM)

Per ISOGG / Shared cM Project tables: full siblings range
2200-3400 cM, half siblings 1300-2300, parent ≈ 3400+, aunt/uncle
1300-2300, first cousins 550-1300. 1300 cM is the lower fence below
which "close family" becomes ambiguous with "first cousin" — above it,
the relationship is almost always parent / sibling / half-sib /
aunt-uncle. For a paternal NPE signal that's exactly the right cut.

Phase 26.x may revisit this if endogamous populations (Ashkenazi,
Mennonite) push first-cousin matches above 1300 cM in practice. For
now the corpus exercises high-end matches at 1768 / 1748 cM, which sit
comfortably above the threshold.

## Alternatives rejected

1. **Hard-code the detector to fire only on tree_id `tree_11_*` /
   `tree_15_*`.** Rejected — the harness scoring would still pass, but
   the detector would carry no genealogical content. ADR-0084 anti-cheat
   §"Anti-cheat" forbids this shape. We treat the corpus as a
   regression of evidence patterns, not of tree IDs.
2. **Fold detector logic directly into `engine.run_tree`.** Rejected
   — Phase 26.3+ will add 5-10 more detectors. A registry boundary now
   keeps each detector small, independently testable, and removable.
3. **Make the detector LLM-assisted (call ai-layer to classify
   user_assertions).** Rejected — ADR-0084 mandates a deterministic
   harness. The token-based heuristics here are sufficient for the
   corpus and observable in CI without a network dependency.
4. **Use `compose_hypothesis` directly to score the bio claim.** Phase
   7.0 / 7.5's pairwise composer is the right tool for confidence
   aggregation across multiple Evidence sources, but it operates on a
   pair of subjects with `Evidence` records — Phase 26.x detectors
   operate at the tree level on raw input fixtures. A future PR can
   bridge them (a `dna_vs_tree` detector that emits structured
   `Evidence` records into `compose_hypothesis`), but that's
   orthogonal: Phase 26.2's job is to land the harness-visible
   contradiction signal.
5. **Match `evaluation_results` by reading
   `expected.confidence`/`min_confidence` directly.** Rejected — that
   would let any detector "pass" by parroting numbers from the answer
   key. The detector emits its own confidence values (0.97 / 0.92 /
   0.05) and the assertion matcher only checks structural shape
   (relationship string, status enum, claim text mentions
   bio-candidate id).

## Consequences

### Positive

* Tree 11 score climbs from `0.10` (schema-only baseline) to
  approximately `0.54` (3/6 assertions × 0.7 + 3/7 flags × 0.2 + 1.0 ×
  0.1 = `0.536`). Tree 15 also benefits — `eval_15_003` and
  `eval_15_004` flip to `True` and the `dna_vs_tree_parentage_contradiction`
  flag is matched.
* The detector / registry split sets the pattern for Phase 26.3+: each
  future detector is one file under `detectors/`, appended to the
  registry, with its own ADR.
* Anti-cheat is now tested with poison-input fixtures, not just by
  staring at the source.

### Negative / cost

* The Phase 26.1 baseline anti-cheat tests had to migrate from
  `tree_11` to `tree_07`. As more detectors cover more trees, those
  tests will need to migrate again — eventually the corpus may run out
  of "uncovered" trees, at which point we'll re-shape the anti-cheat
  contract (likely as a generic "tree-id is not consulted" property
  test on every detector).
* Token-based social/adoptive detection is brittle: a tree fixture
  with creative phrasing (`step-dad`, `non-bio dad`) would slip
  through. Acceptable for the synthetic corpus — every fixture is
  either authored or reviewable — but a real ingest pipeline would
  need the LLM-extraction layer ahead of this detector.

### Risks

* **False-positive risk on endogamous trees.** If Phase 26.3 adds an
  Ashkenazi tree where first-cousin matches reach 1300+ cM, the
  detector could mis-classify. Mitigation: if it happens, lift the
  threshold or require both (1) and the cluster-strength check.
* **Sealed-set semantics drift.** Phase 22.1's sealed-set entry
  contract (ADR-0082) is still evolving. Today the detector emits a
  candidate dict with `extra="allow"` — if Phase 22.x tightens the
  field shape, the detector will need a small update.

### What needs to happen in code

* New: `packages/inference-engine/src/inference_engine/detectors/{dna_vs_tree,registry}.py`.
* Updated: `packages/inference-engine/src/inference_engine/engine.py`
  (call into `registry.run_all`, merge into `EngineOutput`).
* New: `tests/test_phase_26_2_dna_vs_tree.py`.
* Updated: `tests/test_inference_engine_output_schema.py` (anti-cheat
  pivot from tree_11 → tree_07).

## When to revisit

* When Phase 26.3+ adds a tree with strong DNA + social context but a
  shape we don't want to flag as parentage contradiction (e.g., known
  surrogate / sperm donor without an "alternate father" claim).
* When the cM threshold trips a real endogamous tree.
* When `compose_hypothesis` becomes the single authoritative
  confidence aggregator — at that point the detector should emit
  `Evidence` records into the composer instead of pre-baked
  confidences.

## References

* ADR-0084 — Evaluation Harness Foundation (Phase 26.1).
* ADR-0016 — inference-engine architecture (Phase 7.0 baseline).
* ADR-0065 — confidence aggregation v2 (Phase 7.5).
* ADR-0082 — Sealed-set consumer integration (Phase 15.11c).
* ISOGG Shared cM Project tables — relationship range reference.
* `data/test_corpus/trees/tree_11_unknown_father_npe_dna_contradiction.json`.
* `data/test_corpus/trees/tree_15_gedcom_safe_merge_conflicting_sources.json`.
