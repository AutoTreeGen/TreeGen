# ADR-0094: Mennonite colony founder-loop detector

## Status

Accepted for Phase 26.11.

## Context

Mennonite colony DNA clusters can reflect founder effects, endogamy and regional
community structure. These signals must not be inserted as direct ancestry or
used to create fictional bridge persons without primary records.

The target corpus case is:

`tree_13_mennonite_colony_founder_loop_ambiguity`

It models:

- a fictional Ludmila Friesen bridge;
- Anna Friesen incorrectly inserted as mother of Gregory Batensky;
- same-name Anna Friesen records from different colonies/parents;
- Wiens/Friesen/Jantzen/Schmidt Mennonite cluster evidence;
- Batensky/Dodatko Slavic paternal anchor evidence;
- online-tree bridge conflicts with Orthodox records.

## Decision

Implement `mennonite_founder_loop.detect(tree)` as a deterministic detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_dna_matches`
- Mennonite colony/church register snippets
- Orthodox/civil BDM snippets
- online-tree bridge evidence

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `fictional_bridge_person`
- `mennonite_jewish_or_slavic_boundary_error`
- `pedigree_collapse_mennonite_colony_founder_loop`
- `same_name_different_person_colony_context`
- `pedigree_collapse_endogamy_small_segment_overuse`
- `online_tree_fictional_bridge`
- `direct_pedigree_insertion_blocked`

It also emits confirmed Batensky/Dodatko branch claims, separate probable
Mennonite cluster claims, rejected merge decisions and quarantined bridge claims.

## Consequences

Tree 13 should rise from baseline to complete score. This strengthens
AutoTreeGen's handling of Mennonite founder populations and prevents fictional
bridge ancestors from being added to sealed pedigrees.
