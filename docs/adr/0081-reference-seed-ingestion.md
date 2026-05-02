# ADR 0081 — Reference Seed Data Ingestion (Phase 22.1b)

* Status: Accepted
* Date: 2026-05-02
* Phase: 22.1b
* Hard dep: alembic 0042 (Phase 24.4) ✓ in main

## Context

Phase 22.x introduces evidence-aware research workflows. Several
sub-phases need shared reference data that the user's research draws
on:

| Reference | Use |
|---|---|
| Country archive directory | Phase 22.1c+: cross-reference archive_registry rows to known archives per country. |
| Surname variant clusters (455) | Phase 22.x evidence validators + dedup blocking — fuzzy match against canonical surnames per community scope. |
| Surname transliteration table (86) | Phase 10.9e + 22.x — fallback transliteration when 15.10 generic table doesn't cover a rarity form. |
| Fabrication-detection patterns (61) | Phase 22.x evidence validators — flag pedigrees matching known-bad patterns (Baal Shem Tov "descent", patronymic confusion, Holocaust reconstruction errors, etc.). |
| Eastern-European place lookup (505) | Phase 22.x + 24.x — old → modern place name resolution for archive search and report generation. |

Phase 22.1 (`#193`) — the canonical `archive_registry` table — is **not
yet in main** (CONFLICTING). The brief explicitly downgrades the
"22.1 в main" hard-dep to "graceful fallback if missing". This ADR
records the carve-out: 22.1b ships seed tables independently;
Phase 22.1c will cross-reference them once 22.1 lands.

## Decision

Add a new `packages/seed-data/` workspace member that owns:

* **5 SERVICE_TABLES** (alembic 0043) for the seed data:
  * `country_archive_directory_seed` (PK `iso2`)
  * `surname_variant_seed` (PK `(canonical, community_scope)`)
  * `surname_transliteration_seed` (PK `source_form`)
  * `fabrication_pattern_seed` (PK `pattern_id`, indexed by `category`)
  * `place_lookup_seed` (PK `(old_name, modern_country)`)
* **Idempotent ingest CLI** `seed-data ingest` (`python -m seed_data`).
  Two modes:
  * `--canonical-only` (default): loads files committed in
    `packages/seed-data/data/canonical/` only — always works in CI.
  * `--all`: also loads larger local-only files via `--data-dir` flag
    or `SEED_DATA_DIR` env var (mirror of `GEDCOM_TEST_CORPUS`).
* **Hybrid commit/gitignore split** for the source JSONs:
  * Committed (small, ~220 KB total): `country_v1.json` (14 countries)
    * `fabrication_patterns.json` (61 patterns).
  * Gitignored (larger, ~1.4 MB total): surname clusters, place
    lookup, country v2 batches, USSR extension, competitive analysis
    (doc-only, not ingested).
* **Synthetic mini-fixtures** (`tests/fixtures/seed/synthetic_*.json`)
  for CI shape coverage of the local-only files without committing
  them.

### Why a separate workspace member

* Keep `archive-service` deploy unit small. The ingest is a one-off CLI,
  not a runtime endpoint. No FastAPI / httpx deps; pure
  `shared-models` + `pydantic` + `sqlalchemy[asyncio]` + `typer`.
* Allow Phase 22.1 (`#193`), 22.5 (in main), and 22.1b to evolve
  independently. Specifically, this PR does not touch
  `services/archive-service/` — it stays mergeable behind whichever
  archive-service work lands next.

### Why all 5 in `SERVICE_TABLES` (not `TREE_ENTITY_TABLES`)

Per `feedback_orm_allowlist`: SERVICE_TABLES are non-tree-domain
service-internal rows. Reference seeds carry no `tree_id`, no
soft-delete semantics, no provenance chain — they are shared
read-only reference managed via CLI refresh, not user edits. Mirrors
`document_type_weights` (Phase 22.5) and `waitlist_entries` (Phase 4.12)
patterns.

### Idempotency via `ON CONFLICT DO UPDATE`

Each `upsert_*` function does one bulk insert with PostgreSQL's
`INSERT ... ON CONFLICT (natural_pk) DO UPDATE`. Re-running the CLI
on identical seeds = same row count, `updated_at` refreshes. Safe to
include in deploy pipelines or run after every JSON refresh.

### `iso2` widened to `String(8)`

Brief assumed strict ISO 3166-1 alpha-2; v1 file actually emits at
least one historical composite code (`AT-GAL` for Austria-Galicia).
Pydantic + ORM + alembic all use `max_length=8` to accept those
without lossy normalization. Standard alpha-2 entries remain 2 chars;
the widening is forward-compatible.

## Alternatives rejected

1. **Co-locate ingest in `services/archive-service/`.** Bloats the
   service deploy unit with one-off CLI code; makes 22.1b stack on
   `#193` (which is CONFLICTING). Memory `feedback_no_stacked_prs`
   forbids stacking on in-flight PRs.
2. **Commit all 5 JSONs.** ~1.6 MB diff of personal-WIP reference
   data. Memory `project_owner_design_materials_private` says owner
   curates these locally; not all should be public-repo material.
   Hybrid split keeps the small canonical set committed for CI while
   honoring the privacy posture for the larger curated files.
3. **Keep all 5 JSONs local-only.** CI would have no real-data
   coverage of the loaders for canonical files; would need to maintain
   a synthetic fixture for every shape including the small
   high-leverage ones (fabrication patterns specifically — those drive
   evidence validators). Not worth it for ~220 KB of canonical data.
4. **Wait for 22.1 (`#193`) to merge.** Geoffrey demo on 2026-05-06
   is in 4 days; 22.1c can ship as a follow-up once 22.1 lands.

## Consequences

* `SEED_DATA_DIR` env var introduced — owner sets it on dev machine;
  CI doesn't, and the ingest CLI's `--all` flag explicitly errors when
  unset rather than silently skipping.
* `coordinate_precision="approximate_seed"` — ~88% of place-lookup
  rows are 10–50 km off the true location. Schema docstring + README
  * this ADR all explicitly warn: **NOT for automated geo-matching**;
  only UI hints + manual verification.
* No FK from `country_archive_directory_seed.iso2` to
  `archive_registry.country_iso2` yet — that's Phase 22.1c after
  `#193` lands. Schema doesn't include the FK column; loose coupling
  by `iso2` value will work transparently.
* Wheel for `seed-data` ships canonical JSONs as package data
  (`include = "data/canonical/**/*.json"` in pyproject) — installable
  via `uv pip install -e .` and the CLI works without a checkout of
  the monorepo.

## Risks

* **Place lookup precision** — most pernicious risk. Mitigated via
  schema docstring + README + ADR warnings; consumer code (Phase 22.x
  geo-search, Phase 24.x archive recommendation) must check
  `coordinate_precision` before using `lat/lon`.
* **Fabrication patterns are an opinionated list.** False positives
  will happen — `confidence_when_flagged` (0–1] is a multiplicative
  factor, not a verdict. Consumer evidence-validator combines with
  context-specific signals.
* **Surname clusters are seed-only**, not an authority file (per the
  source file's own metadata: "intended for fuzzy search, blocking,
  and candidate generation; they must be scored against dates,
  places, religion, DNA, and sources"). Same posture in code.

## Future work

* **Phase 22.1c** (after `#193` merges): wire
  `country_archive_directory_seed.iso2` ↔ `archive_registry.country_iso2`
  with a non-FK app-level lookup; expose unified country-archives
  view in archive-service.
* **Phase 22.x evidence validators** consume
  `fabrication_pattern_seed` to emit warning on pedigrees that match
  known-bad patterns.
* **Phase 24.x archive recommendation** consumes
  `place_lookup_seed` to translate a person's place_of_birth (often
  an old name) into modern country / region for archive routing.
* **v2 country batches** (UA/BY/RU expanded; batches 2–5 will land
  later) drop in as additional files in `data/reference/`; the CLI
  already loops `country_reference_database_v2_batch*.json` glob.
* **Versioned seed refresh policy** — TBD when v2 seeds land in main.
  Likely: bump file content + re-run ingest in deploy pipeline; the
  `updated_at` column tells consumers when a row last changed.
