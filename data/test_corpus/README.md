# AutoTreeGen Test Tree Corpus (Phase 26.1)

Synthetic deterministic test tree corpus for evidence-engine regression
testing. **Not** real personal data — every person, place, and DNA value is
fabricated for research/test purposes.

This is **product-level evaluation data**, committed to the repo (with an
explicit `.gitignore` exception — the rest of `data/` is ignored).

> Source: `autotreegen_test_tree_corpus_trees1_20_complete.zip`
> (ChatGPT-generated package, 2026-05-02). See ADR-0084 for the rationale.

## Layout

```text
data/test_corpus/
├── README.md                          ← this file
├── trees/                             ← 20 individual tree fixtures
│   ├── tree_01_pale_levitin_npe_resolution.json
│   ├── tree_02_mennonite_batensky_fictional_bridge.json
│   ├── …
│   └── tree_20_full_pipeline_sealed_set_contradiction_resolution.json
├── harness/                           ← expected-output specifications
│   ├── autotreegen_evaluation_harness_trees1_20.json
│   ├── autotreegen_test_tree_corpus_index.json
│   ├── autotreegen_test_tree_corpus_index.md
│   └── autotreegen_eval_runner_skeleton.py    (reference; canonical
│                                                 runner is scripts/run_eval.py)
├── combined/
│   └── autotreegen_test_tree_corpus_trees1_20_combined.json
└── manifest/
    └── autotreegen_test_tree_corpus_manifest.json   (sha256 + sizes)
```

## What each tree exercises

| Tree | Category                                          | Phase coverage                  |
| ---- | ------------------------------------------------- | ------------------------------- |
| 01   | NPE via DNA + rabbinical bridge fabrication       | DNA, fabrication filter         |
| 02   | Cross-religious fictional bridge + Mennonite      | Quarantine, endogamy            |
| 03   | Maiden-name identity resolution                   | Entity resolution               |
| 04   | Viral-tree contamination (fabrication detection)  | Fabrication filter              |
| 05   | Brest-Litovsk Holocaust gap reconstruction        | Place + chronology              |
| 06   | Rabbi Kamenetsky hypothesis (not confirmed)       | Hypothesis lifecycle            |
| 07   | Patronymic vs. surname disambiguation             | Name normalization              |
| 08   | Maiden vs. married name resolution                | Name + DNA                      |
| 09   | Cross-platform DNA match resolver                 | DNA cross-vendor                |
| 10   | Historical place jurisdiction resolution          | Place engine                    |
| 11   | Unknown-father NPE + DNA contradiction            | NPE biological/social split     |
| 12   | Ashkenazi endogamy multi-path relationship        | Endogamy correction             |
| 13   | Mennonite colony founder loop ambiguity           | Pedigree collapse               |
| 14   | Sephardic/Mizrahi crossover false Ashkenazi merge | Ethnic-cluster guard            |
| 15   | GEDCOM safe merge with conflicting sources        | Round-trip + provenance         |
| 16   | Metric-book OCR extraction errors                 | Source extraction               |
| 17   | Revision-list household interpretation            | Source extraction               |
| 18   | Immigration name-change myth + wrong origin       | Myth filter                     |
| 19   | Famous-line royal/rabbinical overclaim filter     | Quarantine                      |
| 20   | Full pipeline sealed-set contradiction            | Sealed sets + full integration  |

## Running the harness

```powershell
uv run python scripts/run_eval.py                    # all 20
uv run python scripts/run_eval.py --tree tree_11_unknown_father_npe_dna_contradiction
uv run python scripts/run_eval.py --fail-under 0.5
```

The runner writes a JSON report to `reports/eval/autotreegen_eval_report.json`.
Phase 26.1 baseline scores are near zero — this is by design. Phase 26.2+
detectors will lift score tree by tree.

## Privacy + provenance

* No real persons. All names/places/DNA values are synthetic.
* The Pale-of-Settlement geography, Ashkenazi/Sephardic naming patterns,
  and historical jurisdictions are real research references; the
  individuals and family lines are not.
* Trees 01–03 use an early format (no `assertion_id`); trees 04–20 use the
  current format (`evaluation_assertions[].assertion_id` of form
  `eval_NN_NNN`). The runner synthesizes IDs for trees 01–03 by index.

## Replacing or extending

* `manifest/autotreegen_test_tree_corpus_manifest.json` pins sha256 of every
  source file. To replace the corpus (e.g. add tree 21):
  1. Update files in `trees/`, `harness/`, etc.
  2. Regenerate the manifest with new hashes.
  3. Bump `version` in the harness JSON.
  4. Update tests (test count assertion) and runner if schema changed.
* Do **not** add real personal data here. Use synthetic only.

## See also

* ADR-0084 — Evaluation harness foundation (Phase 26.1).
* `packages/inference-engine/src/inference_engine/engine.py` — `run_tree`.
* `packages/inference-engine/src/inference_engine/output_schema.py` —
  output contract.
* `scripts/run_eval.py` — the runner.
