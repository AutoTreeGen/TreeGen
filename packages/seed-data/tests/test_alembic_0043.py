"""Alembic 0043 — table + index existence post-upgrade (Phase 22.1b)."""

from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def test_seed_tables_exist_after_migration(postgres_dsn: str) -> None:
    """All 5 seed tables created with expected columns + index."""
    engine = create_async_engine(postgres_dsn)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: set(inspect(sync_conn).get_table_names())
            )
            expected = {
                "country_archive_directory_seed",
                "surname_variant_seed",
                "surname_transliteration_seed",
                "fabrication_pattern_seed",
                "place_lookup_seed",
            }
            assert expected.issubset(tables), f"missing: {expected - tables}"

            # Spot-check column shapes per table.
            country_cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"]
                    for c in inspect(sync_conn).get_columns("country_archive_directory_seed")
                }
            )
            assert {"iso2", "country", "v2_batch", "raw_data"}.issubset(country_cols)

            surname_cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_columns("surname_variant_seed")
                }
            )
            assert {
                "canonical",
                "community_scope",
                "variants_latin",
                "variants_cyrillic",
                "variants_hebrew",
                "variants_yiddish",
                "raw_data",
            }.issubset(surname_cols)

            place_cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_columns("place_lookup_seed")
                }
            )
            assert {
                "old_name",
                "modern_country",
                "lat",
                "lon",
                "coordinate_precision",
            }.issubset(place_cols)

            # Index on fabrication category.
            fab_indexes = await conn.run_sync(
                lambda sync_conn: {
                    ix["name"] for ix in inspect(sync_conn).get_indexes("fabrication_pattern_seed")
                }
            )
            assert "ix_fabrication_pattern_seed_category" in fab_indexes
    finally:
        await engine.dispose()
