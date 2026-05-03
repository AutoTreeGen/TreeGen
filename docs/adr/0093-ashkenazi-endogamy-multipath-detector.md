# ADR-0093: Ashkenazi endogamy multi-path relationship detector

## Status

Accepted for Phase 26.10.

## Context

Ashkenazi Jewish DNA relationships can be inflated or ambiguous because of
endogamy, pedigree collapse and multiple relationship paths. A single total-cM
estimate is not enough to force one relationship path when shared matches and
triangulated segments support separate collateral branches.

The target corpus case is:

`tree_12_ashkenazi_endogamy_multi_path_relationship`

It models:

- a multi-path cousin connected through Levitin-Friedman and Katz-Feldman paths;
- separate triangulated segments on chromosome 6 and chromosome 11;
- Levitin-only and Katz-Feldman anchor matches;
- a small-segment endogamy/noise match that must not become a proof anchor;
- a public tree that compresses the relationship into a single Levitin path.

## Decision

Implement `ashkenazi_endogamy.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `input_dna_matches`
- `embedded_errors`
- shared matches
- triangulated segments
- cM / longest segment / segment count
- archive snippets connecting each probable path

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `pedigree_collapse_ashkenazi_single_path_error`
- `pedigree_collapse_endogamy_small_segment_overuse`
- `public_tree_single_path_overcompression`
- `multi_path_relationship_required`
- `katz_feldman_cluster_not_noise`
- `shared_match_cluster_split`
- `triangulated_segments_support_distinct_paths`

It also emits probable relationship-path claims, multi-path model claims and
quarantined single-path/small-segment proof claims.

## Consequences

Tree 12 should rise from baseline to complete score. This becomes the foundation
for endogamy-aware relationship prediction in Jewish genealogy and other
endogamous populations.
