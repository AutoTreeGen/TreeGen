"""TTL purge для expired bundle blobs + ORM rows (Phase 24.4).

Запускается hourly arq cron'ом (см. :mod:`report_service.worker`)
или явно тестами (frozen time).

Алгоритм:

1. SELECT ``id, tree_id, storage_url, output_format`` WHERE
   ``ttl_expires_at < now``.
2. Для каждого row: storage.delete(storage_url) если задан.
3. DELETE FROM report_bundle_jobs WHERE id = ...
4. Возвращает количество purged.

Errors при storage.delete не блокируют DB-cleanup — иначе orphan blob
удержит всю очередь. Logged + skipped.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from shared_models.orm import ReportBundleJob
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from report_service.bundles.storage import get_bundle_storage

_LOG = logging.getLogger(__name__)


async def purge_expired_bundles(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    storage: Any | None = None,
    now: dt.datetime | None = None,
) -> int:
    """Удалить bundle row'ы (+ blobs) с ``ttl_expires_at < now``.

    Returns:
        Количество purged row'ов.
    """
    actual_now = now or dt.datetime.now(dt.UTC)
    actual_storage = storage or _maybe_storage()

    async with session_factory() as session:
        res = await session.execute(
            select(
                ReportBundleJob.id,
                ReportBundleJob.storage_url,
            ).where(ReportBundleJob.ttl_expires_at < actual_now)
        )
        rows = list(res.all())

        for _row_id, storage_url in rows:
            if storage_url and actual_storage is not None:
                try:
                    await actual_storage.delete(storage_url)
                except Exception:  # pragma: no cover — log + continue
                    _LOG.warning(
                        "purge: storage.delete failed for %s — orphan blob",
                        storage_url,
                    )

        if rows:
            ids = [row_id for row_id, _ in rows]
            await session.execute(delete(ReportBundleJob).where(ReportBundleJob.id.in_(ids)))
            await session.commit()

    _LOG.info("purge_expired_bundles: removed %d rows", len(rows))
    return len(rows)


def _maybe_storage() -> Any | None:
    """Best-effort: return storage handle, swallow env-config errors.

    Cron sweeps shouldn't crash if STORAGE_BACKEND isn't configured —
    log + DB-only cleanup.
    """
    try:
        return get_bundle_storage()
    except Exception:  # pragma: no cover
        _LOG.warning("purge: storage backend unavailable; DB-only cleanup")
        return None


__all__ = ["purge_expired_bundles"]
