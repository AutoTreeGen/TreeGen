# Phase 5.2 discovery — FS pedigree merger hook

> **Status:** Discovery checkpoint, no code yet.
> **Author:** @autotreegen (FS-merger agent)
> **Date:** 2026-04-27
> **Purpose:** Capture the actual state of dedup infrastructure on
> `main` so the next agent (post-`/clear`) plans Phase 5.2 from facts,
> not from the stale `docs/agent-briefs/phase-5-2-fs-pedigree-merger.md`.

## TL;DR

The brief assumes a **persisted `duplicate_suggestions` review queue**
exists and that Phase 5.2 just plugs FS-import into it. **It does not
exist**. The Phase 4.5 «review queue» is a pure on-demand computation;
Phase 4.6 ships a manual-merge backend (`PersonMergeLog`) but no
suggestion table. Real Phase 5.2 work therefore needs an architectural
decision before code:

- **Add a generic suggestions table** (brief-literal, biggest scope).
- **Wire FS-import → existing on-demand finder + return candidates inline**
  (smallest scope, no schema change, no idempotency / cooldown).
- **Add a narrow FS-only attempts table** for idempotency + cooldown
  without inventing a generic queue (compromise).

Recommended starting point: **(c)** — see «Recommendation» below.

## What's actually on main (PR refs as of 2026-04-27)

### Phase 5.0 — FamilySearch client (PR #25, #29, #34, #40)

`packages/familysearch-client/` — OAuth PKCE, `get_person`, `get_pedigree`,
typed errors, tenacity retry. Mock-tested only; sandbox key TODO.

### Phase 5.1 — FS importer (PR #65, #72)

`services/parser-service/src/parser_service/services/familysearch_importer.py`:

```python
async def import_fs_pedigree(
    session: AsyncSession,
    *,
    access_token: str,
    fs_person_id: str,
    tree_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    generations: int = 4,
    fs_client: FamilySearchClient | None = None,
    fs_config: FamilySearchConfig | None = None,
) -> ImportJob:
```

- Provenance on imported `Person.provenance`: `{source: "familysearch",
  fs_person_id, fs_url, imported_at, import_job_id}`. **Field name is
  `fs_person_id`, not `fs_pid` as the brief writes.**
- Idempotency for FS↔FS already shipped via `_existing_fs_person_ids` —
  selects by `Person.provenance->>'fs_person_id'`. Re-imports refresh,
  don't duplicate.
- `ImportJob.stats` carries `persons` (new inserts) and
  `persons_refreshed` (idempotent updates). Distinguishing fresh vs
  refresh is cheap from the caller side.

API: `services/parser-service/src/parser_service/api/familysearch.py`,
`POST /imports/familysearch` returns `ImportJobResponse`. The response
schema (`schemas.py:16-29`) **does not** carry suggestion-related
fields — `duplicate_suggestions_created` / `review_url` would be
additive.

### Phase 3.4 / 4.5 — dedup finder + on-demand listing (no DB queue)

`services/parser-service/src/parser_service/services/dedup_finder.py`:

```python
async def find_person_duplicates(
    session: AsyncSession,
    tree_id: uuid.UUID,
    threshold: float = _DEFAULT_THRESHOLD,
    *,
    use_blocking: bool = True,
) -> list[DuplicateSuggestion]:
```

- Loads **all** persons in the tree (`_load_persons_for_matching`),
  buckets by Daitch-Mokotoff phonetic code, runs pairwise
  `entity_resolution.person_match_score`.
- Returns in-memory list of `DuplicateSuggestion` (Pydantic, not ORM).
- Per-tree, no per-import scoping; no persistence.

`services/parser-service/src/parser_service/api/dedup.py`:

```python
@router.get("/trees/{tree_id}/duplicate-suggestions", ...)
async def list_duplicate_suggestions(
    tree_id, session,
    entity_type: EntityType | None = None,
    min_confidence: float = 0.80,
    limit: int = 100,
    offset: int = 0,
) -> DuplicateSuggestionListResponse:
    """Запустить dedup-scoring и вернуть paginated suggestions."""
```

Re-runs the scorer on every request. **No state is stored**, no
`status="reviewed"`, no `rejected_at`, no idempotency between runs.

`DuplicateSuggestion` and `DuplicateSuggestionListResponse` in
`schemas.py:193-225` are Pydantic only:

```python
class DuplicateSuggestion(BaseModel):
    entity_type: EntityType  # source | place | person
    entity_a_id: uuid.UUID
    entity_b_id: uuid.UUID
    confidence: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
```

### Phase 4.6 — manual merge backend (PR #81, ADR-0022)

Shipped:

- `packages/shared-models/src/shared_models/orm/person_merge_log.py` —
  `PersonMergeLog` ORM. Audit trail + 90-day undo window.
- `services/parser-service/src/parser_service/services/person_merger.py` —
  `preview` → `commit` → `undo` flow with explicit `confirm: True`
  (Pydantic literal). CLAUDE.md §5 enforced as code (no auto-merge).
- `services/parser-service/src/parser_service/api/persons.py` — four
  endpoints: preview, commit, undo, merge-history.
- `Person.merged_into_person_id` (already in initial schema, Phase 0)
  is now actually used.

This is **manual merge of two specific persons**, not a queue. It does
not know «here is a list of pairs the user should consider next». A
caller picks two person IDs and walks the flow.

### Entity-resolution scorer — `packages/entity-resolution`

```python
@dataclass(frozen=True, slots=True)
class PersonForMatching:
    given: str | None
    surname: str | None
    birth_year: int | None
    death_year: int | None
    birth_place: str | None
    sex: str | None  # 'M' / 'F' / 'U' / 'X' / None

def person_match_score(a, b) -> tuple[float, dict[str, float]]:
    ...  # composite + components
```

The DTO is BD-free, so any FS-side hook can build it from `FsPerson` +
ORM `Person` rows trivially.

## What the Phase 5.2 brief assumes that's wrong

| Brief claim | Reality |
|---|---|
| `duplicate_suggestions` table exists. Insert rows with `subject_id`, `candidate_id`, `score`, `reason`, `status`. | No such ORM. `DuplicateSuggestion` is Pydantic-only. Persisting suggestions = new ORM + Alembic migration. |
| «Phase 4.5 review queue» persists pending suggestions across requests. | The queue is computed on-demand. There is no «pending» state, no `rejected` status, no time. |
| `provenance.fs_pid` for idempotency. | Stored as `provenance.fs_person_id`. Helper `_existing_fs_person_ids` already in importer — Phase 5.2 must not duplicate. |
| Phase 4.6 «infrastructure» = a queue. | Phase 4.6 is the **manual two-person merge** flow (`PersonMergeLog`). No queue. The merge endpoints are reusable as the *terminal* step (after a user picks a candidate pair), but they do not store suggestions. |
| 90-day cooldown on `rejected` suggestions. | No `rejected_at` column anywhere. Requires schema. (`PersonMergeLog` has its own 90-day window for **undo of executed merges**, which is a different thing.) |

## What is genuinely missing (architectural)

1. **Where do FS-flagged candidates live between import and review?**
   - In the response only? (ephemeral)
   - In a DB queue? (new schema)
   - Recomputed on every `GET /trees/{id}/duplicate-suggestions`?
     (current behaviour — fine for Phase 4.5 UI but loses idempotency
     hints for FS-imports specifically.)
2. **Cross-source idempotency**: re-importing the same FS person should
   not re-suggest the same candidate pair. Cheap if persisted; needs a
   workaround if not.
3. **Cooldown on rejected**: requires *some* persistent state per pair.
4. **Generic vs FS-specific**: should the schema be a generic
   `duplicate_suggestions` table (helps Phase 4.5 too) or narrow
   `fs_dedup_attempts` (just for this flow)? This is the crux question.

## Three options to plan against

### Option A — Brief-literal: generic `duplicate_suggestions` table

Adds:

- `duplicate_suggestions(id, tree_id, entity_type, entity_a_id,
  entity_b_id, score, reason, status, components, evidence,
  rejected_at, last_seen_at, created_at, updated_at)`.
- Alembic migration 0006.
- `dedup_finder` writes through this table; `list_duplicate_suggestions`
  reads from it (with optional re-compute trigger).
- FS importer post-step calls a new helper that writes FS-flagged rows
  with `reason="fs_import"`.
- Cooldown filter on read.

✅ Brief-aligned. Helps Phase 4.5 evolve from on-demand to persisted
without re-architecting later.
❌ Largest schema change. Semantics of «when do we recompute» need an
ADR (e.g. ADR-0023). Touches `shared-models/orm/`. Affects Phase 4.5
read endpoint.

### Option B — Wire-only, ephemeral

- FS importer post-step calls `find_person_duplicates` scoped to the
  just-imported person rows (small subset, fast) using the existing
  `PersonForMatching` DTO.
- Returns candidates in the response: `duplicate_candidates: [...]`.
- Frontend shows them inline; user clicks → existing Phase 4.6 preview
  endpoint.

✅ Smallest change. No schema, no migration, no agent collision.
❌ No idempotency. Re-import → same candidates again. No cooldown.
Doesn't satisfy Phase 5.2 brief tasks 3 (rules 2 & 3).

### Option C — Narrow FS-only attempts table

Adds:

- `fs_dedup_attempts(id, tree_id, fs_person_id, candidate_person_id,
  score, status [pending|merged|rejected], rejected_at, created_at,
  updated_at)`.
- Migration 0006 (just this table).
- FS importer post-step writes one row per (FS person, candidate)
  whose score ≥ threshold and where (fs_person_id, candidate_person_id)
  isn't already `merged` or `rejected_at` within last 90 days.
- Response: `duplicate_suggestions_created: int`, `review_url:
  "/trees/{id}/duplicates"` (existing on-demand UI page; FS-attempts
  surface there as a separate filtered section in a future small UI
  PR).

✅ Idempotency + cooldown semantics with the smallest ORM diff.
✅ Doesn't preempt a future generic-queue ADR by Phase 4.5; both can
coexist or merge later (FS attempts → seeds for the generic queue).
✅ Stays consistent with the FS-provenance pattern already in the
importer.
❌ Two-table world (FS attempts + future generic queue) until 4.5
migrates. Worth a brief ADR to document the seam.

## Recommendation

**Start with (c)**. It satisfies the brief's idempotency + cooldown
without inventing a generic queue, doesn't fight Phase 4.5's current
on-demand model, and keeps the schema diff small enough for one PR.

Defer the generic-queue ADR to a separate Phase 4.5.x ticket («persist
suggestions»), referencing this discovery doc.

## Hook points for the implementer

When the focused brief lands, these are the touch points (verbatim):

- `services/parser-service/src/parser_service/services/familysearch_importer.py`:
  add a post-step after `set_audit_skip(..., False)` (around the
  `job.stats = {...}` block, ~line 470) that:
  1. SELECTs newly-inserted FS person ids from `person_rows_to_insert`
     (already a local variable; no extra query).
  2. Calls a new helper `find_fs_dedup_candidates(session, tree_id,
     fs_person_ids, threshold=0.6)` (new module
     `services/fs_dedup.py`).
  3. Persists `FsDedupAttempt` rows.
  4. Adds `duplicate_suggestions_created` to `job.stats` (already
     `dict[str, int]`).

- `services/parser-service/src/parser_service/api/familysearch.py`:
  add `review_url` to a new response model (or extend
  `ImportJobResponse` carefully — it's shared with the GEDCOM importer).

- `packages/shared-models/src/shared_models/orm/`: new
  `fs_dedup_attempt.py` model + add to `__init__.py`.

- `infrastructure/alembic/versions/`: new revision after
  `0005_person_merge_logs.py`.

- Tests in `services/parser-service/tests/test_fs_dedup.py` (this is
  the file the brief names; doesn't yet exist).

## Pitfalls to flag in the next brief

1. Pydantic `DuplicateSuggestion` already exists in `schemas.py` — do
   **not** name a new ORM `DuplicateSuggestion` (collision). The
   ORM in option A would need a different Python class name (e.g.
   `DuplicateSuggestionRecord`) or live in a different module path.
2. `ImportJobResponse.stats` is typed `dict[str, int]`. Don't add
   string fields to stats; add structured fields at the top level
   instead (the FS importer already learned this lesson — see Phase
   5.1 commit).
3. Don't enumerate every person × every other person on every import.
   Scope the score to (just-imported FS persons) × (existing non-FS
   persons in the tree) and reuse `_load_persons_for_matching` (or a
   subset variant of it).
4. CLAUDE.md §5 is now enforced in code (Phase 4.6 ADR-0022). FS auto-
   merge for high-confidence pairs is **explicitly out of scope** —
   only suggestions, never merge.
5. `provenance.fs_person_id` is the canonical idempotency key for
   FS↔FS. FS↔local idempotency uses (`tree_id`, `fs_person_id`,
   `candidate_person_id`) — this is the natural unique constraint on
   the new table in option C.
