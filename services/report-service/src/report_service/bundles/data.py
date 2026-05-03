"""ORM CRUD для :class:`ReportBundleJob` (Phase 24.4).

Атомарные счётчик-инкременты идут одним SQL-statement'ом
(``UPDATE ... SET completed_count = completed_count + 1 ...``) —
read-modify-write на Python-side небезопасен под concurrent jobs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from shared_models.orm import (
    BundleOutputFormat,
    BundleStatus,
    ReportBundleJob,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# TTL по умолчанию: 7 дней с момента создания (per ADR-0078 §"storage cost").
DEFAULT_TTL_DAYS: int = 7


async def create_bundle_job(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    requested_by: uuid.UUID,
    relationship_pairs: list[dict[str, Any]],
    output_format: str,
    confidence_threshold: float | None,
    now: dt.datetime | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> ReportBundleJob:
    """INSERT bundle-job row в status=queued.

    ``ttl_expires_at`` = ``now + ttl_days`` (default 7d). Caller должен
    flush/commit и enqueue arq task после.

    ``relationship_pairs`` — уже сериализованный jsonb-friendly список
    (UUID'ы как str, claimed_relationship как str-значение enum'а).
    """
    actual_now = now or dt.datetime.now(dt.UTC)
    job = ReportBundleJob(
        tree_id=tree_id,
        requested_by=requested_by,
        status=BundleStatus.QUEUED.value,
        output_format=output_format,
        relationship_pairs=relationship_pairs,
        confidence_threshold=confidence_threshold,
        total_count=len(relationship_pairs),
        completed_count=0,
        failed_count=0,
        error_summary=None,
        storage_url=None,
        started_at=None,
        completed_at=None,
        ttl_expires_at=actual_now + dt.timedelta(days=ttl_days),
    )
    session.add(job)
    await session.flush()
    return job


async def load_bundle_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    tree_id: uuid.UUID | None = None,
) -> ReportBundleJob | None:
    """SELECT row by id (опц. фильтр по tree_id для permission gate)."""
    stmt = select(ReportBundleJob).where(ReportBundleJob.id == job_id)
    if tree_id is not None:
        stmt = stmt.where(ReportBundleJob.tree_id == tree_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def mark_running(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    now: dt.datetime | None = None,
) -> None:
    """``queued → running`` + ``started_at = now``."""
    actual_now = now or dt.datetime.now(dt.UTC)
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(status=BundleStatus.RUNNING.value, started_at=actual_now)
    )


async def increment_completed(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
) -> None:
    """Атомарный ``completed_count += 1`` (single SQL statement)."""
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(completed_count=ReportBundleJob.completed_count + 1)
    )


async def increment_failed(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    error_entry: dict[str, Any],
) -> None:
    """Атомарный ``failed_count += 1`` + append entry в ``error_summary``.

    error_summary handling: SQL-уровневый append через JSONB не bullet-proof
    под concurrency, поэтому read-modify-write внутри transaction. Под
    arq-worker нагрузкой это OK — ровно один job mutates его error_summary.
    """
    res = await session.execute(
        select(ReportBundleJob.error_summary).where(ReportBundleJob.id == job_id)
    )
    row = res.first()
    current = list(row[0] or []) if row else []
    current.append(error_entry)
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(
            failed_count=ReportBundleJob.failed_count + 1,
            error_summary=current,
        )
    )


async def mark_completed(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    storage_url: str,
    now: dt.datetime | None = None,
) -> None:
    """``running → completed`` + ``storage_url`` + ``completed_at``."""
    actual_now = now or dt.datetime.now(dt.UTC)
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(
            status=BundleStatus.COMPLETED.value,
            storage_url=storage_url,
            completed_at=actual_now,
        )
    )


async def mark_failed(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    now: dt.datetime | None = None,
) -> None:
    """``running → failed`` + ``completed_at`` (даже на failure — finished marker)."""
    actual_now = now or dt.datetime.now(dt.UTC)
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(
            status=BundleStatus.FAILED.value,
            completed_at=actual_now,
        )
    )


async def mark_cancelled(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    now: dt.datetime | None = None,
) -> None:
    """``* → cancelled``. API-call DELETE."""
    actual_now = now or dt.datetime.now(dt.UTC)
    await session.execute(
        update(ReportBundleJob)
        .where(ReportBundleJob.id == job_id)
        .values(
            status=BundleStatus.CANCELLED.value,
            completed_at=actual_now,
        )
    )


__all__ = [
    "DEFAULT_TTL_DAYS",
    "BundleOutputFormat",
    "BundleStatus",
    "create_bundle_job",
    "increment_completed",
    "increment_failed",
    "load_bundle_job",
    "mark_cancelled",
    "mark_completed",
    "mark_failed",
    "mark_running",
]
