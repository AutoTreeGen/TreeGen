# AI sync — Claude results log

> Append-only log of what Claude has shipped, started, or attempted-and-stopped.
> ChatGPT reads this each session to know what's already done before drafting
> new briefs. Keep entries terse — the diff and the PR are the source of
> truth; this is the index.

**Last entry:** 2026-05-03

---

## How to append

One entry per logical deliverable (PR, brief, audit, decision). Format:

```text
### YYYY-MM-DD — short title

- **Kind:** PR / brief / ADR / audit / refactor / fix / docs / discovery
- **Phase / scope:** Phase X.Y or "cross-cutting"
- **Branch / PR:** `feat/...` → #NNN (state: open / merged / dirty / closed)
- **Files touched:** key paths only
- **Outcome:** one sentence on what now exists / works / changed
- **Followups:** anything open, with pointer to `04-blockers.md` if blocked
```

Newest entries on top. Don't rewrite history; if something later changes,
append a follow-up entry referencing the original.

---

## Entries

### 2026-05-03 — AI-sync coordination docs

- **Kind:** docs
- **Phase / scope:** cross-cutting (workflow infrastructure)
- **Branch / PR:** `feat/phase-26-1-eval-harness-foundation` (uncommitted)
- **Files touched:**
  - `docs/ai-sync/00-HANDOVER.md` (new)
  - `docs/ai-sync/04-blockers.md` (new)
  - `docs/ai-sync/06-claude-results.md` (new — this file)
  - `CLAUDE.md` (added §0 ai-sync pointer)
  - `CHATGPT.md` (new at repo root)
- **Outcome:** Two-AI workflow now has a committed shared surface in
  `docs/ai-sync/`. Replaces ad-hoc `.chatgpt-export/` private dumps for
  coordination state (the dumps stay private — only the live coordination
  docs are tracked). `CLAUDE.md` and `CHATGPT.md` both point at it as the
  load-first directory.
- **Followups:** none — these are scaffold docs; both AIs maintain them
  going forward.

### 2026-05-03 — Phase 26.1 evaluation harness foundation (WIP)

- **Kind:** PR (work in progress, uncommitted)
- **Phase / scope:** Phase 26.1 — deterministic evaluation harness foundation
- **Branch / PR:** `feat/phase-26-1-eval-harness-foundation` → not yet pushed,
  zero commits ahead of `main` at the time of writing
- **Files touched (worktree, uncommitted):**
  - `packages/inference-engine/src/inference_engine/__init__.py` (modified)
  - `packages/inference-engine/src/inference_engine/engine.py` (new) —
    `run_tree(tree)` baseline; returns required keys with empty lists and
    `evaluation_results = {assertion_id: False}` for every assertion.
  - `packages/inference-engine/src/inference_engine/output_schema.py` (new) —
    Pydantic `EngineOutput` with `extra="forbid"` at top level,
    `extra="allow"` on nested claim models for forward-compat.
    `REQUIRED_OUTPUT_KEYS` frozenset + `validate_output()` helper.
  - `packages/inference-engine/src/inference_engine/detectors/` (new dir,
    skeleton)
  - `scripts/run_eval.py` (new) — discovers `data/test_corpus/trees/*.json`,
    loads harness, runs engine per tree, scores
    `0.7 * assertion + 0.2 * flag + 0.1 * schema`, writes JSON report to
    `reports/eval/autotreegen_eval_report.json`. Supports `--tree`,
    `--fail-under` (default 0.0), `--output`.
  - `data/test_corpus/` (new) — corpus extracted from
    `autotreegen_test_tree_corpus_trees1_20_complete.zip` per brief layout
    (`trees/`, `harness/`, `combined/`, `manifest/`).
  - `reports/eval/autotreegen_eval_report.json` (artefact — gitignored per
    `.gitignore` line 193).
  - `.gitignore` (modified) — re-includes `data/test_corpus/**` and
    whitelists `reports/eval/.gitkeep`.
- **Outcome:** Baseline engine + runner + corpus in place. Runner executes
  end-to-end; baseline overall score is near zero by design (Phase 26.2+
  detectors will lift it tree by tree). Engine output contract is locked via
  Pydantic.
- **Followups (blocking commit):**
  - Tests not yet authored: `tests/test_corpus_eval_runner.py`,
    `tests/test_inference_engine_output_schema.py` (both listed in brief
    §TESTS).
  - ADR not yet created. Brief says `ADR-0026-01-evaluation-harness-foundation.md`
    but engine docstrings reference `ADR-0084`. Need to lock the chain number
    via `scripts/next-chain-number.ps1` and reconcile both sides before
    commit.
  - Then full `scripts/check.ps1` run before push.
  - Tracked as **B-2026-05-03-01** in `04-blockers.md`.
