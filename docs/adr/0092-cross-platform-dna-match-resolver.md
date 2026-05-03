# ADR-0092: Cross-platform DNA match resolver

## Status

Accepted for Phase 26.9.

## Context

The same DNA match may appear on multiple platforms under different names,
profile IDs, kit IDs or aliases. Conversely, two people can share a surname or a
family cluster without being the same individual.

The target corpus case is:

`tree_09_cross_platform_dna_match_resolver`

It models:

- AncestryDNA, MyHeritage and GEDmatch entries for the same Adrienne Kaplan match;
- Geoff Michael as same Levitin/Kaplan family cluster but distinct person;
- an FTDNA A. Kaplan with a different email hash, weaker cM and Galician context;
- public tree over-merging same-cluster people;
- endogamous/surname small-segment collisions.

## Decision

Implement `cross_platform_dna_match.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `input_dna_matches`
- `embedded_errors`
- shared matches
- email hashes
- kit IDs
- platform IDs
- cM / segment evidence
- archive snippets connecting the cluster to a branch

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `same_name_different_person`
- `shared_cluster_not_identity`
- `surname_only_identity_merge_risk`
- `public_tree_same_cluster_person_merge_error`
- `endogamy_small_segment_overuse`
- `cross_platform_identity_resolved`
- `kit_id_email_hash_match_confirmed`

It also emits confirmed/rejected merge decisions, family-cluster relationship
claims and quarantined public-tree/surname collision claims.

## Consequences

Tree 09 should rise from baseline to complete score. This becomes a core layer
for cross-platform DNA identity, shared-match cluster mapping and future
endogamy-aware relationship prediction.
