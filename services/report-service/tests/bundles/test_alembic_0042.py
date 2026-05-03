"""Alembic migration 0042 (report_bundle_jobs) — table + indexes + CHECK invariants.

Не запускает up/down full-cycle (alembic handles that in conftest); просто
проверяет, что миграция создала ожидаемую structure'у при upgrade head'а.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


async def test_report_bundle_jobs_table_exists_after_migration(
    postgres_dsn: str,
) -> None:
    """0042 must produce a table with the expected name + columns + checks."""
    engine = create_async_engine(postgres_dsn)
    try:
        async with engine.connect() as conn:
            tables: list[str] = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
            assert "report_bundle_jobs" in tables

            cols = await conn.run_sync(
                lambda sync_conn: {
                    c["name"] for c in inspect(sync_conn).get_columns("report_bundle_jobs")
                }
            )
            expected = {
                "id",
                "tree_id",
                "requested_by",
                "status",
                "output_format",
                "relationship_pairs",
                "confidence_threshold",
                "total_count",
                "completed_count",
                "failed_count",
                "error_summary",
                "storage_url",
                "created_at",
                "updated_at",
                "started_at",
                "completed_at",
                "ttl_expires_at",
            }
            assert expected.issubset(cols), f"missing: {expected - cols}"

            indexes = await conn.run_sync(
                lambda sync_conn: {
                    ix["name"] for ix in inspect(sync_conn).get_indexes("report_bundle_jobs")
                }
            )
            assert "ix_report_bundle_jobs_tree_status_created" in indexes
            assert "ix_report_bundle_jobs_ttl" in indexes

            # CHECK constraint must reject mismatched total_count. Возможные типы:
            # asyncpg CheckViolationError, sqlalchemy IntegrityError — DB driver
            # stack даёт оба в зависимости от версии; широкий guard допустим.
            from sqlalchemy.exc import DBAPIError

            with pytest.raises(DBAPIError):
                await conn.execute(
                    text(
                        "INSERT INTO report_bundle_jobs "
                        "(id, tree_id, requested_by, relationship_pairs, "
                        "total_count, ttl_expires_at) "
                        "VALUES (gen_random_uuid(), gen_random_uuid(), "
                        "gen_random_uuid(), '[]'::jsonb, 1, now())"
                    )
                )
    finally:
        await engine.dispose()
