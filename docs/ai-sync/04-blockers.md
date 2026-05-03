# AI sync — blockers

> Live register of things blocked, waiting, or in flight between sessions.
> Both Claude and ChatGPT update this. Lean toward over-recording — a stale
> "(resolved 2026-05-01)" line is cheaper than a forgotten dependency.

**Last reviewed:** 2026-05-03

---

## How to use this file

Each entry is one heading with:

- **Status** — `OPEN` / `WAITING` / `RESOLVED` / `SUPERSEDED`
- **Owner** — who unblocks (owner / Claude / ChatGPT / specific agent slot)
- **Opened** — date opened
- **What** — the blocker in one sentence
- **Detail** — context, evidence, links to PRs / files / commits
- **Next step** — concrete action that would unblock

When a blocker resolves, change status to `RESOLVED`, add a one-line outcome,
keep it for one weekly review cycle, then move to the archive at the bottom.

---

## Open blockers

### B-2026-05-03-01 — Phase 26.1 evaluation harness uncommitted

- **Status:** OPEN
- **Owner:** Claude (in flight on `feat/phase-26-1-eval-harness-foundation`)
- **Opened:** 2026-05-03
- **What:** Phase 26.1 implementation (engine + runner + corpus + ADR) is
  written in the worktree but not committed; branch has zero commits ahead of
  `main`.
- **Detail:**
  - Untracked / modified files: `packages/inference-engine/src/inference_engine/engine.py`,
    `packages/inference-engine/src/inference_engine/output_schema.py`,
    `packages/inference-engine/src/inference_engine/detectors/`,
    `packages/inference-engine/src/inference_engine/__init__.py` (modified),
    `scripts/run_eval.py`, `data/test_corpus/`, `reports/eval/`,
    `phase-26-1-evaluation-harness-foundation-brief.md`.
  - Brief: `phase-26-1-evaluation-harness-foundation-brief.md`.
  - ADR target: `docs/adr/0084-evaluation-harness-foundation.md` — created.
  - ADR references reconciled to `ADR-0084`.
    yet created — note that root brief refers to it as ADR-0084 in the
    docstrings; reconcile chain number via
    `scripts/next-chain-number.ps1` before commit).
- **Next step:**
  1. Run `scripts/next-chain-number.ps1` to lock the actual ADR number.
  2. Reconcile docstring references in `engine.py` / `output_schema.py` /
     `run_eval.py` to the locked ADR number.
  3. Add `tests/test_corpus_eval_runner.py` and
     `tests/test_inference_engine_output_schema.py` per brief §TESTS.
  4. Run `scripts/check.ps1` (full local CI mirror).
  5. Commit + push + PR.

### B-2026-05-02-01 — PR #181 (Voice-to-Tree frontend) DIRTY

- **Status:** WAITING (carry-over from 2026-05-02 overnight)
- **Owner:** brief author / agent that produced PR #181
- **Opened:** 2026-05-02 (~01:28)
- **What:** PR #181 (slot 10-9d voice-to-tree-frontend) is `OPEN-DIRTY` /
  CONFLICTING since PR #183 landed. CI was green at last check; auto-merger
  cannot help until rebased.
- **Detail:** Branch hasn't moved since 00:58 the night of 2026-05-02. No
  agent commits since the conflict appeared.
- **Next step:** Either the brief author rebases / resolves, or owner promotes
  a coordinator to do mechanical-conflict resolution under the
  "owner-not-git-orchestrator" override (per memory).
  Verify before any rebase that conflicts are mechanical, not semantic.

### B-2026-05-02-02 — PR #177 / PR #178 long queue

- **Status:** WAITING (soft signal)
- **Owner:** auto-merger (no human action yet)
- **Opened:** 2026-05-02
- **What:** PR #177 (10-8 mcp-server) and PR #178 (5-7a gedcom-diff) have been
  BEHIND-GREEN (CI success, just queued) for ~90 min while #183 / #184 landed
  ahead of them.
- **Detail:** Auto-merger ordering appears non-FIFO. Possible causes: phase /
  dependency ordering, smaller-diff-first heuristic, or a sticky condition on
  these two specific branches. Below the 5h wake threshold (need 5h + ≥3 GREEN).
- **Next step:** Glance in the morning of 2026-05-03; if still queued and now
  ≥5h with ≥3 other GREEN PRs, escalate to owner.

### B-2026-05-02-03 — Phase 5.6 GEDCOM compat — agent stalled

- **Status:** OPEN
- **Owner:** owner (restart decision)
- **Opened:** 2026-05-02
- **What:** Worktree `TreeGen-wt/phase-5-6-gedcom-compat` was created ~02:00
  but branch sits at parent `45d41b7` with zero agent commits ~2h 15m later.
- **Detail:** Brief 7 of the 2026-05-02 sprint. Confidently stalled (not slow).
- **Next step:** Owner restart in a fresh CCC window with the same brief.
  Don't let a sibling agent take it over (per "no-launch-other-agents"
  feedback).

---

## Waiting on owner

*(Decisions / approvals / inputs that block AI work. Empty when nothing.)*

- *(none currently)*

---

## Waiting on ChatGPT

*(Things Claude needs ChatGPT to produce — briefs, fixtures, reference data.)*

- *(none currently)*

---

## Waiting on Claude

*(Things ChatGPT or owner needs Claude to deliver. Mirrored in
`06-claude-results.md` once shipped.)*

- *(see B-2026-05-03-01 for the active deliverable)*

---

## Recently resolved (last 7 days)

*(Move to `archive/` after one weekly review cycle.)*

- *(none yet — file initialized 2026-05-03)*
