"""arq-pool helper для enqueue из API-слоя.

Mirror ``parser_service.queue`` (но без Cloud Tasks-бэкенда — Phase 24.4 v1
живёт только локально + Cloud Run-staging, прод-Cloud-Tasks fan-out
будет в 24.5+ если понадобится).

Использование::

    from report_service.queue import enqueue_bundle_job

    await enqueue_bundle_job(job_id=str(job.id))
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from arq import create_pool

from report_service.worker import QUEUE_NAME, _redis_settings_from_env

if TYPE_CHECKING:
    from arq.connections import ArqRedis


# Кэш arq-пулов по event-loop'у — копия parser-service паттерна.
# Один процесс может иметь несколько loop'ов (тесты, юпитер); пул,
# созданный в одном loop, нельзя использовать в другом.
_pools: dict[int, ArqRedis] = {}
_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """Singleton ``ArqRedis`` для текущего event-loop'а."""
    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    cached = _pools.get(loop_id)
    if cached is not None:
        return cached

    async with _lock:
        cached = _pools.get(loop_id)
        if cached is not None:
            return cached

        pool = await create_pool(
            _redis_settings_from_env(),
            default_queue_name=QUEUE_NAME,
        )
        _pools[loop_id] = pool
        return pool


async def close_arq_pool() -> None:
    """Закрыть кэшированные пулы (FastAPI shutdown / fixture-teardown)."""
    pools = list(_pools.values())
    _pools.clear()
    for pool in pools:
        await pool.close(close_connection_pool=True)


async def enqueue_bundle_job(*, job_id: str) -> None:
    """Поставить ``generate_report_bundle_job`` в очередь.

    ``deduplication_key`` через ``_job_id`` гарантирует, что повторный
    enqueue того же ``job_id`` — no-op (защита от ретраев API-слоя).
    """
    pool = await get_arq_pool()
    await pool.enqueue_job(
        "generate_report_bundle_job",
        job_id,
        _queue_name=QUEUE_NAME,
        _job_id=f"bundle:{job_id}",
    )


__all__ = ["close_arq_pool", "enqueue_bundle_job", "get_arq_pool"]
