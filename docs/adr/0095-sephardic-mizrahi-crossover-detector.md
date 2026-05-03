# ADR-0095: Sephardic/Mizrahi crossover false Ashkenazi merge detector

## Status

Accepted for Phase 26.12.

## Context

Jewish genealogy must preserve population context. Broad Jewish DNA overlap,
similar surnames and modern Israel co-location do not prove that Bukharian,
Mountain Jewish/Juhuro or other non-Ashkenazi Jewish clusters belong inside a
Pale Ashkenazi branch.

The target corpus case is:

`tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge`

It models:

- Ashkenazi Rabinovich/Ginzburg/Kaplan branch in Minsk/Vilna context;
- Bukharian Rabinov/Kaplunov cluster from Bukhara/Samarkand;
- Mountain Jewish/Juhuro cluster from Caucasus context;
- public tree collapse of all Israeli Rabinovich/Rabinov profiles;
- Kaplan/Kaplunov surname collision;
- broad Jewish DNA overlap treated as branch proof.

## Decision

Implement `sephardic_mizrahi_crossover.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_dna_matches`
- ethnicity/population context
- archive snippets
- public-tree source-quality evidence
- surname collision metadata

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `non_ashkenazi_jewish_crossover_false_ashkenazi_merge`
- `mountain_jewish_cluster_not_pale_ashkenazi`
- `broad_jewish_dna_overlap_not_branch_proof`
- `same_name_place_name_false_equivalence`
- `public_tree_population_context_collapse`
- `kaplan_kaplunov_false_equivalence`
- `population_context_required`

It also emits confirmed Ashkenazi branch claims, separate population-cluster
claims, rejected merge decisions and quarantined public-tree/surname collision
claims.

## Consequences

Tree 14 should rise from baseline to complete score. This strengthens
AutoTreeGen's population-aware Jewish genealogy reasoning and prevents false
Ashkenazi merges.
