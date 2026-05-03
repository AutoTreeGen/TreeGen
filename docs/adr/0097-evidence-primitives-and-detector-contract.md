# ADR 0097 — Evidence Primitives & Detector Contract Helpers (Phase 27.1)

* Status: Accepted
* Date: 2026-05-03
* Phase: 27.1
* Authors: AutoTreeGen
* Tags: `inference-engine`, `evidence`, `anti-cheat`, `refactor`, `detectors`

## Context

Phase 26 shipped 9 deterministic detectors against the 20-tree
evaluation corpus. The detectors work — overall harness score is
`0.4279`, with 7 trees at `1.0` — but they accumulated repeated
patterns and one specific **anti-pattern** that needs to be locked
down before further phases.

### Repeated patterns (low-risk)

Each detector contains its own copy of:

* `_embedded_errors(tree)` — 6 copies, identical.
* `_archive_snippets(tree)` — 6 copies, identical.
* `_dna_matches(tree)` — 3 copies, identical.
* `_combined_text(snippets)` — 5 copies, near-identical (default
  fields are `("transcription_excerpt", "type", "language")` in 4 of
  5; `metric_book_ocr` uses just `("transcription_excerpt",)`).
* `_safe_list(value)` — 2 copies (mine, in `dna_vs_tree` and
  `gedcom_safe_merge`).

### Anti-pattern (high-risk)

Six detectors read **answer-key fields** at runtime and emit them as
their own output:

| Detector | Reads | Use |
|---|---|---|
| `metric_book_ocr` | `embedded_errors[].expected_flag` | flag emission |
| `sephardic_mizrahi_crossover` | `embedded_errors[].expected_flag` | flag emission |
| `mennonite_founder_loop` | `embedded_errors[].expected_flag` | flag emission |
| `cross_platform_dna_match` | `embedded_errors[].expected_flag` | flag emission |
| `historical_place_jurisdiction` | `embedded_errors[]` reads + `error.get("reason")` | flag emission + rationale pass-through |
| `revision_list_household` | `embedded_errors[]` reads + `error.get("reason")` | flag emission + rationale pass-through |

`embedded_errors[].expected_flag` is the corpus author's annotation
"this is the engine flag that should fire here". A detector that
iterates `embedded_errors` and emits `expected_flag` directly passes
the harness while doing zero genealogical work. ADR-0084 §"Anti-cheat"
forbids reading `expected_engine_flags` (the top-level answer-key
field); `embedded_errors[].expected_flag` is the same information at
a deeper path, and is equally forbidden.

`error.get("reason")` is the corpus author's human description.
Pass-through into `quarantined_claim["reason"]` etc. is the same
class of cheat: a consumer of the engine output sees author-written
rationales presented as detector reasoning.

### Why a refactor before more detectors

Phase 26 hit "all detectors implemented" without a shared contract.
Adding a tenth detector now would copy the same patterns — both the
benign accessors and the answer-key reads. We need a small leaf
package that:

1. provides safe, single-purpose extractors that **physically cannot
   leak answer-key fields** (they strip them on access),
2. provides anti-cheat regression infrastructure that **pins the
   current cheat surface** so it can only shrink, not grow,
3. does not migrate any existing detector — that's deferred to Phase
   27.2+ to keep blast radius minimal.

## Decision

Land an additive `inference_engine.evidence` package containing:

* **`primitives.py`** — TypedDicts (`EmbeddedError`, `ArchiveSnippet`,
  `DNAMatch`, `UserAssertion`) plus two declarative constants:
  `ANSWER_KEY_TOP_LEVEL_FIELDS` (4 fields) and
  `ANSWER_KEY_NESTED_FIELDS` (3 entries: `embedded_errors` strips
  `expected_flag` / `expected_confidence_when_flagged` / `reason`;
  `input_archive_snippets` strips `expected_use`; `input_dna_matches`
  strips `expected_link`).
* **`extractors.py`** — 4 safe accessors plus `combined_snippet_text`.
  Each accessor strips the documented answer-key sub-fields **on
  read**, returning shallow-copied dicts. The input tree is never
  mutated.
* **`builders.py`** — placeholder module documenting that builders
  are deferred to Phase 27.2.
* **`__init__.py`** — re-exports the public surface.

Plus tests:

* **`tests/_evidence_helpers.py`** — `poison_answer_key(tree)` and
  `assert_detector_ignores_answer_key(detect, tree)`.
* **`tests/test_phase_27_1_evidence_primitives.py`** — 44 tests
  across 7 sections including the pinned cheat-surface diagnostic and
  per-tree corpus-score regression.
* **`tests/conftest.py`** — adds `tests/` to `sys.path` so test
  modules can import `_evidence_helpers` (pytest is configured with
  `--import-mode=importlib`, which doesn't auto-do this).

### Strip-on-access semantics

`evidence.embedded_errors(tree)` returns the contents of
`tree["embedded_errors"]` with `expected_flag`,
`expected_confidence_when_flagged`, and `reason` **not present** in
each item dict. Same idea for the other extractors.

This means:

* A future detector calling `evidence.embedded_errors(tree)` literally
  cannot reach those fields; `item["expected_flag"]` raises
  `KeyError`, and `item.get("expected_flag")` returns `None`.
* Existing detectors with their private `_embedded_errors` copies are
  untouched. Their behavior is identical to pre-PR. The
  corpus-regression test in §F of the test file pins all 20 per-tree
  scores against the pre-PR baseline and fails on any drift.
* Phase 27.2+ migrations that swap a detector's private accessor for
  `evidence.embedded_errors` automatically lose access to the cheat
  fields — the migration that wants to keep cheating now has to do it
  visibly (e.g., direct `tree["embedded_errors"][0]["expected_flag"]`),
  which fails code review.

### Cheat-surface pinning

`KNOWN_ANSWER_KEY_CONSUMERS` is a frozenset of 6 detector module
names, pinned in the test file. The diagnostic test
`test_pinned_cheat_surface_matches_reality` runs each registered
detector across all 20 corpus trees twice — once with `tree`, once
with `poison_answer_key(tree)` — and collects the names of detectors
whose output differs. The collected set must equal the pinned
constant. Drift in either direction fails:

* A new detector that cheats grows the actual set → test fails until
  the detector is fixed or the constant is updated with reviewer
  acknowledgement.
* A migrated detector stops cheating → actual set shrinks → test
  fails until the constant is updated in the migration PR.

This is intentionally a single global set, not per-tree pins. Per-tree
granularity has higher signal but multiplies the surface area to
maintain. Phase 27.2/27.3 may upgrade to per-tree pins once one or
two detectors are migrated and the diagnostic earns its keep — for
now, a single set keeps the PR small.

### What this PR does NOT do

* **No detector rewrites.** All 9 detectors retain their private
  accessors, ad-hoc claim-dict construction, and bespoke anti-cheat
  tests. The corpus-regression test enforces zero behavior change.
* **No claim builders.** Builders for `relationship_claim` /
  `merge_decision` / `place_correction` / `quarantined_claim` /
  `sealed_set_candidate` are deferred. The right shape for each
  builder will be visible only after one or two detectors actually
  adopt extractors and call out for a helper. Premature abstraction
  has been an explicit risk in earlier phases.
* **No engine / output-schema changes.** `EngineOutput` and
  `engine.run_tree` are untouched.

## Alternatives rejected

1. **Wrap accessors but leave answer-key fields readable.** Rejected.
   That captures the benign repetition (3-line accessor copies) but
   lets the cheat pattern persist into Phase 27.2+. The whole point
   of refactoring now is to make the cheat path harder, not just
   shorter. Strip-on-access is a one-line discipline that pays out
   in every future migration.
2. **Force-migrate all 6 cheating detectors in this PR.** Rejected.
   That's a 6-detector blast radius, breaks the corpus eval scores
   for ~7 trees (the 6 cheaters' targets plus their cross-coverage),
   and conflates two concerns: extracting primitives vs deciding what
   the right evidence-based logic is for each tree's pattern. Phase
   27.2+ migrates them one at a time, each PR with a focused review.
3. **Pin per-tree cheat surface from day one.** Rejected for now.
   Per-tree granularity is more diagnostic, but adds 20 frozensets
   to maintain (one per tree) where most are empty. Single global
   set covers the immediate need (pin the cheaters) and leaves the
   per-tree upgrade for later when the migration cadence justifies
   it. (The user explicitly requested global-first, per-tree later.)
4. **Move `_evidence_helpers` into `conftest.py`.** Rejected.
   Conftest's auto-import surface makes the helpers available
   everywhere, but harder to grep for which detector tests opt into
   the anti-cheat assertion. A regular module + sys.path setup is
   more explicit. Phase 27.2 migration PRs will `from
   _evidence_helpers import assert_detector_ignores_answer_key` —
   visible in the diff.
5. **Land builders in this PR but keep them unused.** Rejected as
   premature abstraction. 5 builders × ~30 LOC = 150 LOC of code
   with no client. The first migration PR will introduce the first
   builder it actually needs.
6. **Use Pydantic models for fixture shapes instead of TypedDicts.**
   Rejected. TypedDicts are hint-only and zero-cost at runtime, which
   matches our actual usage pattern (detectors do `dict.get(...)` access
   throughout). Pydantic would impose either runtime validation cost or
   a `model_construct` shortcut that gives no extra safety. TypedDicts
   give `mypy` something to check in Phase 27.2+ migrations without
   changing detector code style.

## Consequences

### Positive

* The cheat surface is now visible in one constant in one test file.
  Adding or removing a cheater requires explicit acknowledgement;
  drift fails CI loudly.
* Phase 27.2+ migrations get a one-line anti-cheat assertion
  (`assert_detector_ignores_answer_key(detect, tree)`) that replaces
  3–4 bespoke poison checks per migrated detector test file.
* The `evidence` package is a leaf with no detector imports
  (enforced by a test). It can be imported from any future
  `inference_engine` module without cycle risk.
* Behavior is unchanged. Per-tree corpus scores match the pinned
  baseline exactly (not approximately — `==`, not `pytest.approx`).
  Eval overall stays `0.4279`.

### Negative / cost

* +6 files, ~700 added lines. No deletions; the 6 private
  `_embedded_errors` / `_archive_snippets` / `_combined_text`
  copies live on for at least one more PR cycle.
* TypedDicts are hint-only. A future author may assume key presence
  and skip `.get(...)` access, raising `KeyError` on a sparse
  fixture. Mitigation in ADR §"Strip-on-access semantics" + module
  docstrings.
* Migration PR template for Phase 27.2+ now has to update both
  `KNOWN_ANSWER_KEY_CONSUMERS` and (when scores legitimately move)
  `PHASE_27_1_BASELINE_SCORES`. Two pins per PR.

### Risks

* **`reason` is on the strip set despite being human-readable text.**
  Three legit uses can be argued: rationale pass-through to UI,
  audit-trail content, debug logs. We strip it because the current
  detectors use it as `quarantined_claim["reason"]` —
  author-written text presented as engine reasoning. If a future
  detector legitimately needs to surface the corpus author's
  rationale, that's a UI-layer concern, not a detector concern; the
  ADR draws that line here. If we were wrong, removing `"reason"`
  from `ANSWER_KEY_NESTED_FIELDS` is a one-line change.
* **`combined_snippet_text` default fields are pinned to
  `("transcription_excerpt", "type", "language")`.** This matches 4
  of 5 existing copies; `metric_book_ocr` uses just
  `("transcription_excerpt",)` and will pass `fields=` explicitly at
  migration. If another detector emerges with a third field-set, the
  default is the wrong default — but the function takes `fields=`,
  so this is a backwards-compatible adjustment.
* **Pinned constants are write-once-per-PR.** Migration PRs that
  forget to update `KNOWN_ANSWER_KEY_CONSUMERS` will fail the
  diagnostic; that's the intended forcing function.

## Phase 27.2 hook

The first migration PR (recommended target:
`historical_place_jurisdiction.py`, smallest detector that exercises
all four shared patterns) does five things:

1. Replace the detector's private `_embedded_errors`,
   `_archive_snippets`, `_combined_text`, `_ordered_flags` with
   `evidence.*` calls (the first three; `_ordered_flags` stays
   detector-local for now).
2. Re-derive the detector's flag emissions from input-evidence only —
   no `error.get("expected_flag")` / `error.get("reason")` calls. Per
   the strip-on-access semantics, those fields are simply not in the
   extractor output.
3. Update `tests/test_phase_26_5_*.py` (or whatever the detector's
   test file is) to add
   `assert_detector_ignores_answer_key(detect, fixture)` and remove
   bespoke poison checks.
4. Update `KNOWN_ANSWER_KEY_CONSUMERS` in this PR's test file to
   remove `"historical_place_jurisdiction"`.
5. Update `PHASE_27_1_BASELINE_SCORES` if and only if the tree-10
   score actually moves (it should not — re-derived logic should
   match expected_flag-driven logic if the corpus is well-formed).

If step 4 doesn't accompany step 1–3, the diagnostic test fails. If
step 5 is needed but missing, the corpus regression fails. Both
gates lock the migration.

## When to revisit

* When 2–3 detectors are migrated and the per-tree cheat-surface
  upgrade becomes worthwhile. Phase 27.3 candidate.
* When a detector legitimately needs a now-stripped field. Discuss
  in the migration PR; either narrow `ANSWER_KEY_NESTED_FIELDS` or
  re-architect the detector to derive that information differently.
* When the first builder lands in Phase 27.2's migration PR — the
  ADR pattern (one builder per migration, scoped to the detector's
  shape) becomes the durable rule. ADR-0098+ for the builder design.
* When `combined_snippet_text` defaults outgrow the canonical 3
  fields. Bump major or split into named variants.

## References

* ADR-0084 — Evaluation Harness Foundation (Phase 26.1).
* ADR-0085 — DNA-vs-Tree Contradiction Detector (Phase 26.2).
* ADR-0086 — GEDCOM Safe-Merge Conflict Detector (Phase 26.3).
* `data/test_corpus/trees/*.json` — 20-tree evaluation corpus.
* `reports/eval/phase_27_1_baseline.json` — pinned per-tree score baseline.
