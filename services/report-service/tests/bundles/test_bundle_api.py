"""HTTP-endpoint tests for bundle routes (Phase 24.4).

Covers: 202 create, GET status snapshot (progress polling proxy via
mid-state row mutation), 409 download-before-complete, 410 download-after-ttl,
DELETE cancel-running.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

import pytest
from report_service.bundles.data import create_bundle_job, load_bundle_job
from shared_models.orm import BundleStatus, ReportBundleJob
from sqlalchemy import update

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


pytestmark = pytest.mark.integration


def _pair(a: uuid.UUID, b: uuid.UUID, claim: str | None = "parent_child") -> dict[str, object]:
    return {
        "person_a_id": str(a),
        "person_b_id": str(b),
        "claimed_relationship": claim,
    }


# ---------------------------------------------------------------------------
# POST → 202
# ---------------------------------------------------------------------------


async def test_post_bundle_returns_202_with_job_id(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path POST: 202 + body shape + DB row created."""
    # arq enqueue would need Redis — stub it.
    from report_service.api import bundles as bundles_mod

    async def _noop_enqueue(*, job_id: str) -> None:  # noqa: ARG001
        return

    monkeypatch.setattr(bundles_mod, "enqueue_bundle_job", _noop_enqueue)

    body = {
        "relationship_pairs": [
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"]),
            _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"]),
        ],
        "output_format": "zip_of_pdfs",
    }
    resp = await app_client.post(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles",
        json=body,
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["total_count"] == 2
    assert uuid.UUID(data["job_id"])
    assert data["queued_at"]


# ---------------------------------------------------------------------------
# GET status
# ---------------------------------------------------------------------------


async def test_get_bundle_status_returns_snapshot(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET on a freshly-created job returns queued snapshot."""
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()

    resp = await app_client.get(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles/{job.id}",
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 200
    snap = resp.json()
    assert snap["status"] == "queued"
    assert snap["total_count"] == 1
    assert snap["completed_count"] == 0
    assert snap["failed_count"] == 0


# ---------------------------------------------------------------------------
# Progress poll — simulate mid-job by mutating row directly
# ---------------------------------------------------------------------------


async def test_progress_tracking_reflects_completed_count(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET reflects partial progress — proxy for live polling."""
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[
                _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"]),
                _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"]),
            ],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()

    # Simulate worker mid-progress: 1 of 2 done.
    async with session_factory() as session:
        await session.execute(
            update(ReportBundleJob)
            .where(ReportBundleJob.id == job.id)
            .values(
                status=BundleStatus.RUNNING.value,
                completed_count=1,
                started_at=dt.datetime.now(dt.UTC),
            )
        )
        await session.commit()

    resp = await app_client.get(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles/{job.id}",
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 200
    snap = resp.json()
    assert snap["status"] == "running"
    assert snap["completed_count"] == 1
    assert snap["total_count"] == 2


# ---------------------------------------------------------------------------
# Download — 409 before complete
# ---------------------------------------------------------------------------


async def test_download_before_complete_returns_409(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """GET /download on queued/running → 409."""
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()

    resp = await app_client.get(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles/{job.id}/download",
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Download — 410 after TTL
# ---------------------------------------------------------------------------


async def test_download_after_ttl_returns_410(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Completed job whose ttl_expires_at is in the past → 410."""
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()
        await session.execute(
            update(ReportBundleJob)
            .where(ReportBundleJob.id == job.id)
            .values(
                status=BundleStatus.COMPLETED.value,
                storage_url=f"relationship-bundles/{seeded_tree['tree_id']}/{job.id}.zip",
                ttl_expires_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
            )
        )
        await session.commit()

    resp = await app_client.get(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles/{job.id}/download",
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 410


# ---------------------------------------------------------------------------
# DELETE — cancel running job
# ---------------------------------------------------------------------------


async def test_cancel_running_job(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DELETE on running job → 204 + status=cancelled."""
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=seeded_tree["tree_id"],
            requested_by=seeded_tree["user_id"],
            relationship_pairs=[_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
            output_format="zip_of_pdfs",
            confidence_threshold=None,
        )
        await session.commit()
        await session.execute(
            update(ReportBundleJob)
            .where(ReportBundleJob.id == job.id)
            .values(status=BundleStatus.RUNNING.value, started_at=dt.datetime.now(dt.UTC))
        )
        await session.commit()

    resp = await app_client.delete(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles/{job.id}",
        headers={"X-User-Id": str(seeded_tree["user_id"])},
    )
    assert resp.status_code == 204

    async with session_factory() as session:
        refreshed = await load_bundle_job(session, job_id=job.id)
        assert refreshed is not None
        assert refreshed.status == BundleStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Auth + permission edge cases
# ---------------------------------------------------------------------------


async def test_post_bundle_401_without_header(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
) -> None:
    body = {
        "relationship_pairs": [_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
        "output_format": "zip_of_pdfs",
    }
    resp = await app_client.post(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles",
        json=body,
    )
    assert resp.status_code == 401


async def test_post_bundle_404_for_non_member(
    app_client: AsyncClient,
    seeded_tree: dict[str, uuid.UUID],
) -> None:
    body = {
        "relationship_pairs": [_pair(seeded_tree["parent_id"], seeded_tree["child_a_id"])],
        "output_format": "zip_of_pdfs",
    }
    resp = await app_client.post(
        f"/api/v1/trees/{seeded_tree['tree_id']}/report-bundles",
        json=body,
        headers={"X-User-Id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404
