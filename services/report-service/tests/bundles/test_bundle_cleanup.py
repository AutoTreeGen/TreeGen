"""TTL purge test (Phase 24.4) — frozen time → expired rows deleted, blobs purged."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

import pytest
from report_service.bundles.cleanup import purge_expired_bundles
from report_service.bundles.data import create_bundle_job
from shared_models.orm import BundleStatus, ReportBundleJob
from sqlalchemy import select, update

if TYPE_CHECKING:
    from shared_models.storage import InMemoryStorage
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


pytestmark = pytest.mark.integration


async def test_ttl_cleanup_removes_expired_rows_and_blobs(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """Two jobs: one expired, one fresh. Sweep removes only the expired one."""
    async with session_factory() as session:
        expired = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[
                {
                    "person_a_id": str(seeded_tree["parent_id"]),
                    "person_b_id": str(seeded_tree["child_a_id"]),
                    "claimed_relationship": "parent_child",
                }
            ],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        fresh = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[
                {
                    "person_a_id": str(seeded_tree["parent_id"]),
                    "person_b_id": str(seeded_tree["child_b_id"]),
                    "claimed_relationship": "parent_child",
                }
            ],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()

        expired_key = f"relationship-bundles/{seeded_tree['tree_id']}/{expired.id}.zip"
        fresh_key = f"relationship-bundles/{seeded_tree['tree_id']}/{fresh.id}.zip"
        await in_memory_storage.put(expired_key, b"expired blob", content_type="application/zip")
        await in_memory_storage.put(fresh_key, b"fresh blob", content_type="application/zip")

        await session.execute(
            update(ReportBundleJob)
            .where(ReportBundleJob.id == expired.id)
            .values(
                status=BundleStatus.COMPLETED.value,
                storage_url=expired_key,
                ttl_expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            )
        )
        await session.execute(
            update(ReportBundleJob)
            .where(ReportBundleJob.id == fresh.id)
            .values(
                status=BundleStatus.COMPLETED.value,
                storage_url=fresh_key,
                ttl_expires_at=dt.datetime(2099, 1, 1, tzinfo=dt.UTC),
            )
        )
        await session.commit()

    fixed_now = dt.datetime(2026, 5, 2, 12, 0, 0, tzinfo=dt.UTC)
    purged = await purge_expired_bundles(
        session_factory,
        storage=in_memory_storage,
        now=fixed_now,
    )
    # `>= 1` rather than `== 1` — другие тесты в той же session-scoped БД
    # могли оставить expired rows (test_download_after_ttl). Гарантия теста —
    # МОЯ expired row purged, моя fresh row сохранена.
    assert purged >= 1

    async with session_factory() as session:
        # Expired row gone
        res = await session.execute(
            select(ReportBundleJob.id).where(ReportBundleJob.id == expired.id)
        )
        assert res.first() is None
        # Fresh row preserved
        res = await session.execute(
            select(ReportBundleJob.id).where(ReportBundleJob.id == fresh.id)
        )
        assert res.first() is not None

    # Blob purged from storage too. InMemoryStorage.get на missing key
    # raises FileNotFoundError (см. shared_models.storage:145).
    with pytest.raises(FileNotFoundError):
        await in_memory_storage.get(expired_key)
    # Fresh blob still present
    assert await in_memory_storage.get(fresh_key) == b"fresh blob"
