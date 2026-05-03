# ADR-0091: Historical place jurisdiction detector

## Status

Accepted for Phase 26.8.

## Context

Genealogical place names must be interpreted by event year, political
jurisdiction, religion/community and archive route. Modern country normalization
can destroy evidence context and send researchers to the wrong archives.

The target corpus case is:

`tree_10_historical_place_jurisdiction_resolution`

It models:

- Brest 1863 incorrectly routed as modern Belarus only;
- Koniuchy 1902 losing Congress Poland / Russian Empire context;
- Ekaterinoslav used for the wrong Soviet-era period;
- Danzig/Gdańsk period confusion;
- Molotschna Mennonite colony routed as generic Ukraine;
- Mennonite DNA cluster merged into Jewish Levitin branch by regional adjacency.

## Decision

Implement `historical_place_jurisdiction.detect(tree)` as a deterministic
detector registered through `inference_engine.detectors.registry`.

The detector may use:

- `embedded_errors`
- `input_archive_snippets`
- event-year place context
- archive source type
- DNA/source-quality warning metadata

The detector must not copy `expected_engine_flags` wholesale.

## Output

The detector can emit:

- `modern_country_for_pre1917_record`
- `partition_jurisdiction_confusion`
- `old_name_used_for_wrong_period`
- `danzig_gdansk_period_error`
- `mennonite_colony_generic_ukraine_error`
- `mennonite_jewish_boundary_error`
- `modern_place_normalization_lost_jurisdiction`
- `archive_routing_by_event_year_required`

It also emits place corrections, cluster-boundary claims and quarantined claims.

## Consequences

Tree 10 should rise from baseline to complete score. This becomes the foundation
for archive routing, migration reasoning, OCR interpretation and DNA cluster
separation across historical jurisdictions.
