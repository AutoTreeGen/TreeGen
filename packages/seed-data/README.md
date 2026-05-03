# seed-data

Phase 22.1b reference data ingestion: country directory + surname variant
clusters + surname transliteration table + fabrication-detection patterns

+ Eastern-European place lookup.

## Architecture

+ **Canonical seeds (committed):** small reference files live in
  `packages/seed-data/data/canonical/` and ship with the wheel.
  CI loads these directly.
+ **Local-only seeds (gitignored):** larger files (surname variant
  clusters ~400 KB, place lookup ~800 KB, country v2 batches) live at
  `data/reference/` on the owner's machine. CLI reads them via
  `--data-dir` flag or `SEED_DATA_DIR` env var.
+ **Synthetic mini-fixtures:** `tests/fixtures/seed/synthetic_*.json`
  give CI coverage of the larger-file shapes without committing the
  data itself (mirrors the `GEDCOM_TEST_CORPUS` env-var pattern).

## Ingest CLI

```sh
# Canonical seeds only (always available, ships with wheel):
uv run seed-data ingest --canonical-only

# All seeds (requires SEED_DATA_DIR or --data-dir for the larger ones):
SEED_DATA_DIR=F:/Projects/TreeGen/data/reference uv run seed-data ingest --all

# Equivalent with explicit path:
uv run seed-data ingest --all --data-dir F:/Projects/TreeGen/data/reference
```

Idempotent: re-running upserts via `ON CONFLICT DO UPDATE` on natural
PKs. Safe to run after every refresh of the source JSONs.

## Tables populated (alembic 0043)

| Table | PK | Source file |
|---|---|---|
| `country_archive_directory_seed` | `iso2` | `autotreegen_country_reference_database*.json` |
| `surname_variant_seed` | `(canonical, community_scope)` | `autotreegen_surname_variant_clusters_merged.json` (local-only) |
| `surname_transliteration_seed` | `source_form` | (transliteration_tables block of clusters JSON, local-only) |
| `fabrication_pattern_seed` | `pattern_id` | `autotreegen_fabrication_detection_patterns.json` |
| `place_lookup_seed` | `(old_name, modern_country)` | `autotreegen_eastern_europe_place_lookup_506_merged.json` (local-only) |

## Caveats

+ Place lookup: ~88% of rows have `coordinate_precision="approximate_seed"`
  (10–50 km off true location). Do NOT use for automated geo-matching;
  UI hints + manual verification only. ADR-0081 §"Risks".
+ Surname clusters and place lookup are **personal-WIP reference**; not
  in git. CI uses synthetic mini-fixtures.
+ No cross-reference to `archive_registry` (Phase 22.1) yet — that's
  Phase 22.1c (follow-up after `#193` lands).

## Tests

```sh
uv run --package seed-data pytest packages/seed-data/tests
```

Integration tests against the local-only seeds skip with a clear message
if `SEED_DATA_DIR` is not set.
