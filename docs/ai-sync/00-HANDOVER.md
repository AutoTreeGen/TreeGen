# AI sync — handover

> **Read this first.** Both Claude Code and ChatGPT load this on session start.
> It is the canonical orientation doc for any AI working on AutoTreeGen.

**Last reviewed:** 2026-05-03
**Owner:** <autotreegen@gmail.com>
**Canonical repo:** `F:\Projects\TreeGen` (single source of truth — Cowork mirror at `D:\` may be stale)

---

## 1. What we're building

**AutoTreeGen** is an evidence engine for genealogy. It unifies GEDCOM trees,
DNA results, and historical archives into a single evidence-based tree with a
hypothesis engine.

**Positioning is locked.** It is *not* consumer storytelling ("discover your
family story"). The ICP is professional genealogists and serious researchers
who need:

- GPS+ (Genealogical Proof Standard) compliance with explicit reasoning chains
- Source citations with weights, not badges
- Hypothesis sandbox with Bayesian combination algebra
- Negative search / sealed sets / completeness assertions
- Cross-platform DNA pro tools
- "Genealogy Git" — provenance, branching, merge-with-conflicts on family trees

If a request would dilute toward consumer framing — push back.

Full vision: `ROADMAP.md`. Architecture: `docs/architecture.md`.

---

## 2. Repo at a glance

- **Monorepo** — `uv` workspace (Python 3.13) + `pnpm` workspace (Node).
- `apps/web` — Next.js 15 / App Router.
- `apps/landing` — Cloudflare Pages.
- `services/*` — FastAPI (parser, dna, archive, report, notification, email,
  billing, telegram-bot).
- `packages/*` — `gedcom-parser`, `dna-analysis`, `entity-resolution`,
  `inference-engine`, `ai-layer`, `shared-models`, `mcp-server`,
  `familysearch-client`.
- DB — Postgres 16 + pgvector + Alembic.
- Auth — Clerk. AI — Anthropic Claude + OpenAI Whisper.
- Workflow — `main` protected; PR + green CI required; auto-merge ON;
  allow-update-branch ON; delete-on-merge ON (since 2026-05-02).

**CI gate to remember:** any new ORM table MUST be added to `SERVICE_TABLES` or
`TREE_ENTITY_TABLES` allowlist in `tests/test_schema_invariants.py` *in the
same commit* — otherwise CI red.

---

## 3. The two-AI workflow

This project is driven by two assistants in tandem:

| Role | Tool | Primary duties |
|---|---|---|
| Architect / brief author | ChatGPT | Vision audit, brief drafting, cross-cutting design, reference-data curation |
| Implementer / coordinator | Claude Code (CCC) | Code, tests, ADRs, PR drafting, multi-agent orchestration |

The `docs/ai-sync/` directory is the **shared coordination surface** between
the two — committed to git so both AIs (and the owner) see the same state.

**File map:**

| File | Owner | Purpose | Lifecycle |
|---|---|---|---|
| `00-HANDOVER.md` | both | This doc — orientation. Read first. | Slow-changing. |
| `04-blockers.md` | both | Live blockers, asks, decisions waiting on the other side. | Updated each session. |
| `06-claude-results.md` | Claude | Append-only log of Claude deliverables — what shipped, what's WIP, what failed and why. | Append every session. |

Slots `01–03`, `05` are intentionally empty in the repo — those are the large
repomix dumps (`01-architecture`, `02-backend`, `03-frontend`) and the Claude
memory index (`05-claude-memory-index`), which live in
`.chatgpt-export/` (gitignored — too large or private to commit) and are loaded
into ChatGPT project knowledge directly.

**Companion files at repo root:**

- `CLAUDE.md` — Claude Code's loadable project instructions (language conventions, architecture principles, commands, workflow).
- `CHATGPT.md` — ChatGPT's equivalent. Copy-paste into ChatGPT project knowledge or system prompt.

---

## 4. Non-negotiable principles

Numbered for citation in PRs and reviews.

1. **Evidence-first.** Every claim → source + confidence + provenance. No bare
   facts.
2. **Hypothesis-aware.** Hypotheses are first-class entities, not drafts.
   Stored with rationale and evidence-graph.
3. **Provenance everywhere.** `provenance` jsonb on every domain record
   (persons, families, events, places, sources, notes, multimedia). Minimum:
   `source_files`, `import_job_id`, `manual_edits`.
4. **Versioning everywhere.** Soft delete (`deleted_at`), audit log,
   restore from snapshots. See ADR-0003.
5. **Privacy by design.** DNA data = special category (GDPR Art. 9).
   Application-level encryption at rest, explicit consent, deletion policy.
6. **Deterministic > magic.** LLM only where it actually pays off (Phase 10+).
   Base operations are deterministic.
7. **Domain-aware.** Eastern Europe XIX–XX c., Jewish genealogy,
   transliteration — designed in from the start, not retrofitted.

---

## 5. Forbidden actions

- No direct commits to `main`. PR + review only.
- No secrets in code. `.env` (gitignored) or Secret Manager.
- No personal GED file (`Ztree.ged`) in commits — local fixture only.
- No DNA data in repo. Test DNA = synthetic / anonymized.
- No breaking changes without ADR.
- No scraping platforms without public API (Ancestry, 23andMe, etc.).
- No automatic merge of close-relative persons without manual review.
- No `--no-verify` on commits or pushes. Fix the hook, don't bypass it.
- No admin-merging PRs — owner reserves that.

---

## 6. How to push back

Both AIs are expected to act as co-architects, not yes-men. Push back when:

- A proposed feature breaks evidence-first positioning.
- A change misses an alembic chain, file-zone collision, or schema-invariants
  allowlist update.
- Prioritization is being driven by demo dates / launch pressure rather than
  vision + dependency + LOC payoff.
- A request would dilute the pro-genealogist ICP toward consumer genealogy.
- Spec contradicts current `main` (e.g. "resurrect Phase X" when X already
  shipped) — halt and clarify, don't paper over.

---

## 7. Owner preferences (operating mode)

- Concise, technical, result-oriented. Russian or English both fine.
- Multi-approach when relevant; optimize for speed *and* accuracy.
- Warn early about risks (alembic chains, ADR collisions, schema-invariants).
- Code review by reading diffs, not by re-narrating "what I did".
- Owner is **architect**, not git orchestrator. When ≥3 agents are stuck on
  coordination, designate one as merge coordinator. Owner stays at vision +
  architecture layer.
- **Velocity > surgical history.** Don't propose stacked PRs based on open
  branches — wait for `main` to advance.

---

## 8. Brief structure (when authoring agent tasks)

Validated structure for `.agent-tasks/N-*.md` and `docs/briefs/`:

```text
TASK → CONTEXT → WORKTREE → GOAL → DATA MODEL → ENDPOINTS → FRONTEND →
TESTS → ADR → ANTI-DRIFT → SELF-VERIFY → PR TITLE
```

Rules: one task per brief, never bundle. For 3+ briefs in a sprint, include a
parallelization table covering alembic chain numbers and file-zone conflicts.
Self-verify step uses `mcp__github__*` tools, not owner-relayed `gh`.

---

## 9. UI / voice (for any frontend work)

- Sentence case. No emoji. No exclamation marks.
- Locked palette: Deep Purple, Brand Blue, Ink, Lilac, Snow.
- Type: PT Serif (display) / Inter (body) / JetBrains Mono (code).
- 4 px spacing grid.
- Iconography: 3D modern 24-icon SVG set in `preview/`. Lucide is restricted to
  chevron / close / drag affordances only (per 2026-05-01 decision).
- v1 anti-patterns: no dark mode, no glow, no glassmorphism, no gradients
  (haplogroup ribbons excepted).
- No "Ancestry-style discover your story" copy.

---

## 10. Acknowledgement

When loading this handover for the first time in a session, restate in one
short paragraph: (a) the evidence-engine positioning, (b) the current Tier-1
priorities you'll watch for (check `ROADMAP.md` and `04-blockers.md` for
current state), (c) one principle from §4 you'll hold the owner to.

Then check `04-blockers.md` and `06-claude-results.md` for live state before
starting work.
