# ADR-0089: Famous-relative and rabbinical overclaim filter

## Status

Accepted for Phase 26.6.

## Context

Public genealogy trees often attach local families to famous rabbinical, royal,
Rashi, Maharal or King David lines without primary bridge records. These claims
can spread across copied trees and become treated as proof.

The target corpus case is:

`tree_19_famous_relative_royal_rabbinical_overclaim_filter`

It models common overclaim patterns:

- public-tree Maharal/Rashi/King David chains;
- unsourced Schneerson/Baal Shem Tov bridges;
- rabbinical surname or title treated as proof of descent;
- tiny DNA matches used to prove medieval descent;
- same-name rabbinical surname false merges;
- public-tree famous descent without 17th-18th century bridge records.

## Decision

Implement `famous_overclaim.detect(tree)` as a deterministic tree-level detector
registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_archive_snippets`
- public-tree snippets
- biographical rabbinical source snippets
- local primary metric/revision evidence

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `royal_rashi_king_david_public_tree_chain`
- `rabbinical_schneerson_to_baal_shem_tov`
- `rabbinical_title_or_surname_as_proof`
- `tiny_dna_match_used_for_medieval_descent`
- `same_name_rabbinical_surname_false_merge`
- `public_tree_famous_descent_no_primary_bridge`
- `famous_descent_quarantine_required`

It also emits confirmed local-branch claims, rejected merge decisions,
quarantined famous-descent claims and selected evaluation assertion results.

## Consequences

Tree 19 should rise from baseline to complete score. The detector strengthens
AutoTreeGen's anti-fantasy genealogy layer and protects sealed trees from
unsupported famous-relative branches.
