# Phase 10.9c-cold — Tree assembly engine, COLD-START mode (post-demo, trigger 2026-05-07+)

> **Status:** DRAFT, untracked. Trigger: after 10.9b ships.
> Per ADR-0064 §«Что нужно сделать в коде» 10.9c-cold.

## TASK

Apply `voice_extracted_proposals` records atomically to a tree (new for cold-start, existing for append). CREATE-only mode V1 (no fuzzy matching against existing persons — that's 10.9c-append after 5.5b + 15.10 ship). Provenance per entity: `source_audio_session_id` + `transcript_offset_ms` + `confidence`.

## CONTEXT (read first)

- `docs/adr/0064-voice-to-tree.md` — §«10.9c»
- `.agent-tasks/07-phase-10.9b-ai-layer-extract.md` — output shape (`voice_extracted_proposals.extracted_facts`)
- `.agent-tasks/01-phase-10.9a-orm-audio-sessions.md` — `AudioSession` ORM
- ADR-0003 (provenance jsonb), Phase 2 (audit_log)

## WORKTREE

```text
cd F:\Projects\TreeGen
git worktree add F:\Projects\TreeGen-wt\phase-10-9c-cold-assembly -b feat/phase-10-9c-cold-assembly
```

## DATA MODEL (alembic)

```sql
CREATE TABLE tree_voice_uncertain (
  id uuid pk default gen_random_uuid(),
  tree_id uuid not null references trees(id) on delete cascade,
  audio_session_id uuid not null references audio_sessions(id) on delete cascade,
  transcript_quote text not null,
  reason text,
  transcript_offset_ms int,
  resolved boolean default false,
  resolved_action text,           -- 'created' | 'discarded' | 'merged_with_<person_id>'
  created_at timestamptz default now()
);
CREATE INDEX ix_voice_uncertain_tree ON tree_voice_uncertain(tree_id, resolved);
-- shared_models allowlist: TreeVoiceUncertain → TREE_ENTITY_TABLES
```

## ENDPOINTS (`services/api-gateway`)

- `POST /api/v1/audio-sessions/{id}/apply-to-tree`
  - body: `{mode: 'cold_start' | 'append', target_tree_id?, ego_person_ref?}`
  - cold_start + tree_id null: create new tree (title from session.title or `Voice tree {date}`)
  - applies persons / relationships / events / places as CREATE
  - sets `tree.owner_person_id` from ego_person_ref if provided (or from extraction's `is_owner` flag if present)
  - returns: `{tree_id, applied_persons, applied_relationships, applied_events, applied_places}`
  - **Atomic transaction — all or nothing**
- `GET /api/v1/audio-sessions/{id}/preview-application?mode=cold_start` — dry-run preview (used by web UI from PR #?? — 04-* brief)

## ASSEMBLY (`services/api-gateway/voice_assembly.py`)

```python
async def apply_extraction_cold_start(
    session_id: UUID,
    target_tree_id: UUID | None,
    ego_person_ref: str | None,
) -> ApplicationResult:
    """1. Resolve target tree (create new if null)
       2. Persons: generate UUIDs, build {tool_ref → uuid}
       3. Places: dedup by canonical_name within session (NOT against existing)
       4. Events: link via maps
       5. Relationships: link via map; create Family records as needed (FAM)
       6. Provenance per entity:
            source_audio_session_id, transcript_offset_ms,
            confidence=avg(asr,nlu), created_via='voice_to_tree_v1'
       7. audit_log entry per entity (idempotent)
       8. flag_uncertain → tree_voice_uncertain (NOT persons table)
    """
```

## TESTS (`tests/services/api-gateway/test_voice_apply.py`)

- Cold-start tree_id=null → new tree + N persons + M relationships
- Twin relationship → both persons in same FAM with twin metadata preserved
- Provenance: every person has source_audio_session_id, transcript_offset_ms
- flag_uncertain → goes to tree_voice_uncertain, NOT persons table
- Atomicity: induce DB error mid-apply → assert full rollback (no partial tree state)
- Permission: user can apply to own tree only (4-eye gate)
- Use 3 curated extractions from 10.9b's tests as input fixtures

## ADR

`docs/adr/00XX-voice-tree-cold-start-assembly.md`:

- Decision: cold-start V1 only — append (10.9c-append) ships after 5.5b + 15.10
- Decision: atomic transaction (all or nothing) — partial state worse than failure
- Decision: uncertain → separate table, NOT persons (false-positive prevention)
- Trade-off: V1 may create duplicate persons within session if extraction misses re-mention. Mitigated by 10.9d review UX (already in main); fixed by 10.9c-append fuzzy match.

## ANTI-DRIFT

- НЕ fuzzy matching against existing persons (это 10.9c-append, after 15.10 ships)
- НЕ conflict UI (это 10.9d frontend territory, уже scaffolded)
- НЕ tree_change_proposals (15.4) integration — direct mutate + audit_log в V1

## SELF-VERIFY

1. `pwsh scripts/check.ps1`
2. `uv run alembic heads` → single head
3. `mcp__github__get_pull_request` / `gh pr view <N>` → MERGEABLE/CLEAN/SUCCESS
4. Manual: complete voice session → trigger NLU → trigger apply → assert tree created with extracted persons + provenance + uncertain entries in their own table
5. Report: «PR `#<N>` ready, cold-start atomically applies extraction → tree with provenance»

## PR TITLE

`feat(api-gateway,shared-models): Phase 10.9c-cold voice-to-tree cold-start assembly`
