# ADR-0096: Full-pipeline sealed-set contradiction detector

## Status

Accepted for Phase 26.13.

## Context

The final Phase 26 corpus case combines multiple genealogy failure modes into
one sealed-set decision: DNA parentage contradiction, adoption/social parentage,
fictional Mennonite bridge, famous rabbinical bridge, historical place errors,
endogamy multi-path modeling, tiny DNA overclaim and compound public-tree
contamination.

The target corpus case is:

`tree_20_full_pipeline_sealed_set_contradiction_resolution`

## Decision

Implement `full_pipeline_sealed_set.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_dna_matches`
- archive snippets
- relationship type evidence
- place correction snippets
- public-tree contamination snippets

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `dna_vs_tree_parentage_contradiction`
- `adoption_foster_guardian_as_parent`
- `fictional_bridge_person`
- `rabbinical_famous_line_bridge`
- `old_name_used_for_wrong_period`
- `modern_country_for_pre1917_record`
- `multi_path_relationship_required`
- `tiny_dna_match_used_for_medieval_descent`
- `compound_public_tree_contamination`
- `sealed_set_biological_parentage_candidate`
- `sealed_set_confirmed_branch_candidate`

It also emits confirmed biological-parentage claims, social/adoptive parentage
claims, confirmed branch claims, place corrections, quarantined public-tree
claims and sealed-set candidates.

## Consequences

Tree 20 should rise to complete score. This completes the Phase 26 inference
detector sprint and prepares Phase 27 architecture refactoring.
