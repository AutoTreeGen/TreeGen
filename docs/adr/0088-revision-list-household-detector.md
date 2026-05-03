# ADR-0088: Revision-list household interpretation detector

## Status

Accepted for Phase 26.5.

## Context

Russian Empire revision lists are household and registration records, not direct
birth/marriage/death records. They often omit female household members in
summary pages, contain age drift, distinguish registered community from actual
residence, and include repeated names across nearby households.

The target corpus case is:

`tree_17_revision_list_household_interpretation`

It models common interpretation errors:

- treating missing female enumeration as disproof of a wife or mother;
- merging same-name people from different households;
- inventing a wife from a revision-list gap;
- overinterpreting age drift as identity conflict;
- confusing registered residence with actual residence;
- treating Raskes/Raskin as enough for merge without household continuity;
- public trees converting revision-list gaps into invented facts.

## Decision

Implement `revision_list_household.detect(tree)` as a deterministic tree-level
detector registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_archive_snippets`
- revision-list snippets
- metric-book snippets used as higher-quality relationship evidence

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `revision_list_missing_female_not_disproof`
- `same_name_same_guberniya_different_household`
- `unknown_wife_invented_from_missing_female_revision`
- `revision_list_age_drift_not_identity_conflict`
- `registered_vs_actual_residence_confusion`
- `raskes_raskin_variant_not_enough`
- `public_tree_revision_list_overreach`

It also emits relationship claims, rejected merge decisions, quarantined claims
and selected evaluation assertion results.

## Consequences

Tree 17 should rise from baseline to complete score. The detector strengthens
AutoTreeGen's archive evidence engine for Russian Empire and Jewish genealogy.
