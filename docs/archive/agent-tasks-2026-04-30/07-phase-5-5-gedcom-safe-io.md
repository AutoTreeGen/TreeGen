# Agent 7 — Phase 5.5: GEDCOM Safe Import/Export (split into 5.5a + 5.5b)

> **Trace-able spec** for handoff between sessions. Phase 5.5 is split because
> the original ~3000-LOC scope is unreviewable in one PR.

## Context

International #1 pain in the cross-language research wave 2: GEDCOM round-trips
between platforms (Ancestry / MyHeritage / Geni / RootsMagic / Family Tree
Maker) lose **sources, parent–child links, custom tags, witnesses, godparents**.
Phase 5.5 removes the data-loss risk by:

1. Quarantining unknown / proprietary tags during import (preserve as-is).
2. Re-emitting them on export so a→DB→a' round-trip is byte-equivalent at
   the structural (AST) level.
3. Pre-export loss simulator that reports what *will* be dropped under a
   target dialect.
4. Validation reports (structural broken-refs + semantic impossible-dates).

## Split

### 5.5a — Quarantine import + AST round-trip (THIS PR)

Scope:

- `RawTagBlock` Pydantic model in `gedcom_parser.models`.
- `GedcomDocument.unknown_tags: tuple[RawTagBlock, ...]` populated at
  parse-time via `quarantine.py` (whitelist-driven walk over the AST).
- ORM `import_jobs.unknown_tags` jsonb column (mirrors 10.2
  `source_extractions.raw_response` pattern).
- Alembic migration `0028_import_jobs_unknown_tags`.
- Round-trip tests on synthetic + real-corpus fixtures
  (`gedcom_real` marker; corpus at `D:/Projects/GED`). **Variant B**
  structural diff (re-parse both, compare token streams) — byte-for-byte
  is impossible after ANSEL → UTF-8 normalization.
- ADR-00XX documenting the unknown_tags storage decision (jsonb on
  import_jobs vs. provenance jsonb per-entity vs. new table).
- Import-runner wiring: after parse, persist `doc.unknown_tags` into
  `ImportJob.unknown_tags`.

Out of scope (5.5b):

- TargetDialect enum + per-dialect support matrix.
- LossSimulator + StructuralValidator + SemanticValidator.
- HTTP endpoints `POST /api/v1/gedcom/simulate-export` and
  `POST /api/v1/gedcom/validate`.
- Entity → record reverse converter (only needed once
  the simulator builds dialect-targeted exports from DB).

### 5.5b — Loss simulator + validators + endpoints (FOLLOW-UP)

Depends on 5.5a merge. Scope:

- `target_dialects.py` — `TargetDialect` enum (ANCESTRY, MYHERITAGE, GENI,
  ROOTSMAGIC, FTM, GEDCOM_555, GEDCOM_551) + per-dialect support matrix
  `dict[tag_path, SupportLevel(FULL|LOSSY|DROPPED)]`.
- `loss_simulator.py` — `LossSimulator(dialect).simulate(document) → LossReport`.
- `validator.py` — `StructuralValidator` + `SemanticValidator` →
  `ValidationReport`.
- Endpoints (versioned `/api/v1/gedcom/`):
  - `POST /api/v1/gedcom/simulate-export {tree_id, target_dialect}` →
    `{tags_full, tags_lossy, tags_dropped, items[]}`.
  - `POST /api/v1/gedcom/validate {file or tree_id}` →
    `{structural_warnings, semantic_warnings, stats}`.
- Per-dialect-fixtures for tests.

## Decisions (frozen)

1. **Storage of unknown_tags:** Pydantic `Document.unknown_tags` (parser side)
   - ORM `import_jobs.unknown_tags` jsonb (persistence side). Mirrors
   10.2 `source_extractions.raw_response`. Existing entity models
   (`Person`, `Family`, `Source`, `Event`, `Citation`, `Note`,
   `MultimediaObject`) **stay frozen** — no new fields.
2. **Round-trip test semantics:** Variant B (structural / token-stream
   equality after re-parse). ANSEL → UTF-8 means byte-diff is impossible
   for real corpus.
3. **Endpoint versioning (5.5b):** keep `/api/v1/` prefix. First versioned
   namespace in `parser-service` — convention drift accepted intentionally
   so future GEDCOM-spec evolution can revision the API surface.
4. **Migration size:** `+200 LOC migration НЕ делаем`. We add a single
   `unknown_tags` jsonb column to existing `import_jobs` table (~30-50 LOC
   migration). No new tables this phase.

## Non-goals

- ❌ Modifying existing `Person`/`Family`/`Source`/`Event` Pydantic
  models in `gedcom_parser.entities`.
- ❌ Conversion logic *between* dialects (this is Phase 5.6 «Compatibility
  Simulator» follow-up).
- ❌ UI selector. API only — UI ships in 5.5c after both back-end PRs land.

## Test plan (5.5a)

- **Unit**: `quarantine_record(record, kind)` returns expected unknown
  tags for synthetic Ancestry/MyHeritage/Geni snippets with `_FSFTID`,
  `_UID`, `_PRIM`, `_TYPE`, etc.
- **Round-trip (gedcom_real)**: each fixture in
  `D:/Projects/GED/{Ancestry,MyHeritage,MyHeritage2025,Michael}.ged`
  parses → AST → write_records → re-parse → structural-equal.
  All `unknown_tags` survive the cycle.
- **Persistence**: `ImportJob.unknown_tags` writes + reads round-trip.

## Self-verify

- `pwsh scripts/check.ps1` (mirror of CI `lint-and-test`).
- `uv run pytest packages/gedcom-parser` — all green; `gedcom_real`
  tests run locally if `GEDCOM_TEST_CORPUS=D:/Projects/GED`.
- `gh pr view --json mergeable,mergeStateStatus` → MERGEABLE / CLEAN.

## ROADMAP cross-ref

§5 «Фаза 1 GEDCOM Parser» gets a new sub-section **§5.5 — Safe Import/Export
(round-trip + loss simulator)** with 5.5a / 5.5b breakdown. Added in the
5.5a PR (single commit).
