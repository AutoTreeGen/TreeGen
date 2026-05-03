# ADR-0090: Immigration name-change myth and wrong-origin detector

## Status

Accepted for Phase 26.7.

## Context

Immigration-era genealogy often contains copied family stories and wrong-origin
attachments. Common patterns include Ellis Island name-change myths, surname-only
parent assignment, same-name immigrants merged across countries, and weak DNA or
surname collisions overriding primary manifests and naturalization papers.

The target corpus case is:

`tree_18_immigration_name_change_myth_and_wrong_origin`

## Decision

Implement `immigration_name_origin.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_archive_snippets`
- passenger manifest snippets
- naturalization snippets
- census snippets
- metric-book birth snippets
- public-tree snippets

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `ellis_island_name_change_myth`
- `immigration_same_name_wrong_origin_attachment`
- `surname_only_parent_assignment`
- `family_story_contradicted_by_primary_records`
- `small_galician_surname_collision`
- `wrong_origin_place_assignment`
- `chain_migration_contact_supports_identity`
- `alias_history_not_new_person`

It also emits confirmed origin/parent/alias claims, rejected merge decisions,
place corrections and quarantined claims.

## Consequences

Tree 18 should rise from baseline to complete score. The detector strengthens
AutoTreeGen's migration/origin reasoning layer.
