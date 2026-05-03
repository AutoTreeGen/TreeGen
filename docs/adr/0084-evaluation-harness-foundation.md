# ADR 0084 — Evaluation Harness Foundation (Phase 26.1)

* Status: Accepted
* Date: 2026-05-03
* Phase: 26.1
* Authors: AutoTreeGen
* Tags: `inference-engine`, `evaluation`, `test-corpus`

## Context

AutoTreeGen is an evidence engine, not consumer storytelling. To build an
evidence engine responsibly we need a regression layer that answers:

> When we add detector X for case Y, do other cases still behave?

We have `compose_hypothesis` (Phase 7.0) for pairwise hypothesis scoring
and `aggregate_confidence` (Phase 7.5) for Bayesian fusion — but those are
unit-level pure functions. There is no test layer that runs the engine
end-to-end against canonical genealogy scenarios (NPE via DNA, fictional
rabbinical bridges, GEDCOM safe merge, OCR'd metric books, revision-list
households, immigration name-change myths, etc.).

A 20-tree synthetic corpus already exists, generated and curated outside
the repo as `autotreegen_test_tree_corpus_trees1_20_complete.zip`. Each
tree is a fully synthetic scenario with: input GEDCOM excerpt, DNA
matches, archive snippets, embedded errors, expected reasoning chain,
expected engine flags, and a list of `evaluation_assertions`. A separate
`autotreegen_evaluation_harness_trees1_20.json` pre-computes per-tree
schema integrity, expected flags, and pass/fail rules.

We need to land the corpus + a deterministic evaluation loop **without**
implementing real detectors yet, so that future Phase 26.x PRs can each
add one detector, watch the harness score climb, and demonstrate they
didn't regress other trees.

## Decision

Land Phase 26.1 as four pieces:

1. **Corpus lives in the repo** at `data/test_corpus/` (with an explicit
   `.gitignore` exception — the rest of `data/` stays ignored). It is
   product-level reference/evaluation data, not unit-test fixture: it is
   directly part of the evidence-engine's regression contract and needs
   to round-trip across machines and CI.
2. **`packages/inference-engine`** owns deterministic reasoning. A new
   `engine.run_tree(tree: dict) -> dict` is the single entrypoint for the
   evaluation harness. A new `output_schema.EngineOutput` Pydantic model
   pins the output contract.
3. **`scripts/run_eval.py`** is the canonical runner. It discovers trees,
   loads the harness, runs `run_tree`, validates output, scores per tree
   and overall, and writes `reports/eval/autotreegen_eval_report.json`.
4. **No real detectors in this PR.** Baseline returns required keys with
   empty lists and `{assertion_id: False}` for every assertion. Score is
   near zero by design — that is the regression baseline against which
   Phase 26.2+ detectors will be measured.

### Package boundary

This PR makes the long-pending inference-engine vs ai-layer split
explicit:

| Package                       | Owns                                                                     |
| ----------------------------- | ------------------------------------------------------------------------ |
| `packages/inference-engine`   | Deterministic evidence reasoning, rules, scoring, **engine output contract**. |
| `packages/ai-layer`           | LLM orchestration, extraction, summarization, prompt/tool workflows.     |

The harness lives entirely on the inference-engine side. It must NOT
import `ai-layer` or call an LLM. UI/services consume the engine output
contract; they MUST NOT duplicate truth logic by re-deriving evidence
from raw inputs.

### Output contract (`EngineOutput`)

Top-level keys, all required, `extra="forbid"` at the top level so a
detector cannot silently introduce a new field that the runner doesn't
score:

```text
tree_id                 str               — round-trips from input
engine_flags            list[str]         — flat machine-readable flag list
relationship_claims     list[obj]         — bio/social/adoptive/...
merge_decisions         list[obj]         — incl. blocked merges
place_corrections       list[obj]         — historical jurisdictions
quarantined_claims      list[obj]         — fabrication / public-tree-only
sealed_set_candidates   list[obj]         — eligible for sealed set
evaluation_results      dict[str, bool]   — assertion_id → pass/fail
```

Nested models use `extra="allow"` so detectors can postpone tightening
their per-claim payload until Phase 26.x. The strict gate is at the
top level.

### Scoring

Per tree: `score = 0.7*assertion_score + 0.2*flag_score + 0.1*schema_score`.

* `assertion_score` — fraction of `evaluation_assertions` where the
  engine's `evaluation_results[assertion_id]` is `True`.
* `flag_score` — `|expected ∩ actual| / |expected|` for `engine_flags`.
* `schema_score` — taken from the harness's pre-computed
  `schema_integrity.required_keys_present` (the harness was generated
  with this knowledge baked in, so we honor it rather than recomputing).

Overall: arithmetic mean across evaluated trees.

`--fail-under` defaults to `0.0` because Phase 26.1's baseline is
expected to be near zero (only schema_score contributes). Phase 26.2+
will tighten the threshold tree-by-tree.

### Anti-cheat

The baseline must NOT pre-pass tests by mirroring `expected_engine_flags`
or `expected_confidence_outputs` from the input fixture. Two guard tests
enforce this:

* `test_baseline_does_not_emit_expected_engine_flags`
* `test_baseline_evaluation_results_all_false`

These tests will keep guarding once Phase 26.2 detectors land, because
real detectors must derive flags/results from the input GEDCOM/DNA/
archive evidence — never by reading the answer key.

### `assertion_id` synthesis for legacy trees

Trees 04–20 use the current schema with explicit `assertion_id` of form
`eval_NN_NNN`. Trees 01–03 use an early prototype format with only
`assertion` text and a boolean `expected`. The engine synthesizes IDs
for the latter as `eval_<tree_num>_<idx+1>` so the runner can match
results uniformly.

We chose to keep trees 01–03 as-is rather than retroactively rewriting
them, to preserve the audit trail of corpus evolution. A future minor
bump (corpus 0.2) may normalize them.

## Alternatives rejected

1. **Put corpus in `tests/fixtures/test_corpus/`.** Rejected because the
   corpus is bigger than test data: it is the authoritative regression
   contract for the evidence engine. Future tools (annotation editor,
   dashboard) will read from `data/test_corpus/`. Burying it in
   `tests/fixtures/` would fight the natural product surface.
2. **Put corpus outside repo (release artifact).** Rejected for two
   reasons: (a) hashing/auditability — the manifest pins sha256 for every
   file, and we want PRs to be able to add/replace trees with that
   provenance visible; (b) CI needs it deterministically, and an external
   download would add a flake surface and a network dependency.
3. **Implement detectors in this PR (Phase 26.1+detectors).** Rejected
   for blast-radius reasons: the harness loop, output schema, and
   anti-cheat tests need to land first so each detector PR can be small
   and cleanly graded. Bundling delays the harness benefit and risks
   conflating "schema is wrong" with "detector is wrong" failures.
4. **Run the harness inside the existing `compose_hypothesis` pipeline
   with a synthetic single hypothesis per tree.** Rejected because tree
   evaluation is multi-claim by nature (one tree → many relationships,
   many merge decisions, many places). The harness contract is wider
   than `Hypothesis` and needs its own envelope.
5. **Skip the Pydantic output schema, use bare dicts.** Rejected. With
   bare dicts a detector could silently emit `engine_flag` (singular) or
   `flags` and the runner would just score it as zero forever. Pydantic
   `extra="forbid"` catches this at validation time.

## Consequences

### Positive

* Every Phase 26.x detector PR can be measured: "tree_11 went from 0.10
  to 0.85" is a concrete deliverable.
* Inference-engine vs ai-layer boundary is now explicit and enforced by
  package layout — not just a comment in CLAUDE.md.
* Corpus is in repo with sha256 manifest; PRs that mutate trees are
  reviewable.
* Anti-cheat tests guard against the sneakiest regression mode (passing
  tests by copying answer keys).

### Negative / cost

* +60 files committed under `data/test_corpus/` (~700 KB; trees + harness
  * index + combined + manifest). Repo size impact is modest.
* `.gitignore` had to switch `data/` → `data/*` to allow re-inclusion of
  `data/test_corpus/`. This is a behavior change for any future
  `data/<something>/` work — files there are still ignored, but the
  matching pattern is now `data/*` instead of `data/`. Documented inline
  in `.gitignore`.
* `scripts/run_eval.py` is the second runner-style script (after
  `seed_db.py`). If we accumulate more, a `scripts/run_*.py` convention
  may want to be formalized — not now.

### Risks

* Corpus drift: ChatGPT-generated source can be regenerated. The manifest
  pins hashes, so any unintended change produces a visible diff. Major
  regenerations should bump corpus `version` in the harness JSON.
* Trees 01–03 schema legacy: if we ever need to programmatically rewrite
  them, the synthesised `assertion_id` mapping in `engine._extract_assertion_ids`
  is the contract — any change there requires regenerating those three
  trees in lock-step.

### What needs to happen in code

* `data/test_corpus/` populated (this PR).
* `packages/inference-engine/src/inference_engine/{engine,output_schema}.py`
  added and re-exported from `__init__.py` (this PR).
* `packages/inference-engine/src/inference_engine/detectors/__init__.py`
  placeholder (this PR; will be populated in Phase 26.2+).
* `scripts/run_eval.py` runner (this PR).
* `tests/test_corpus_eval_runner.py`, `tests/test_inference_engine_output_schema.py`
  (this PR).
* `.gitignore` exception for `data/test_corpus/` and `reports/eval/*.json`
  (this PR).
* `ROADMAP.md` Phase 26 section (this PR).

## When to revisit

* When a Phase 26.x detector PR lands and the score formula coefficients
  (0.7 / 0.2 / 0.1) feel wrong in practice.
* When adding tree 21+ — formalize the corpus expansion process and
  consider auto-regenerating the manifest in CI.
* When the harness output starts being consumed by a UI (annotation
  dashboard, eval dashboard) — `EngineOutput` may need a `version` field
  and we'll bump to v2 with explicit migration.
* When ai-layer detectors enter the picture — the hard inference-engine /
  ai-layer split may need a thinner adapter (e.g., a `HybridEngine` that
  composes deterministic detectors with LLM extraction). Today the
  contract is "harness MUST stay deterministic"; tomorrow we may relax
  that for non-scoring metadata.

## References

* ADR-0016 — inference-engine architecture (Phase 7.0 baseline).
* ADR-0065 — confidence aggregation v2 (Phase 7.5).
* `data/test_corpus/README.md` — corpus user-facing README.
* `data/test_corpus/manifest/autotreegen_test_tree_corpus_manifest.json`
  — sha256 manifest for the source ChatGPT-generated package.
