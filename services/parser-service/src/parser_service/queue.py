"""Хелпер для постановки jobs в arq-очередь со стороны API/HTTP-кода.

Парный модуль к ``parser_service.worker``: воркер потребляет jobs, а этот —
их продюсит. Единый источник правды для ``RedisSettings`` —
``parser_service.worker._redis_settings_from_env`` (см. ADR-0026).

Использование::

    from parser_service.queue import get_arq_pool

    pool = await get_arq_pool()
    job = await pool.enqueue_job("noop_job", {"hello": "world"})
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from arq import create_pool

from parser_service.worker import QUEUE_NAME, _redis_settings_from_env

if TYPE_CHECKING:
    from arq.connections import ArqRedis

# Кэш пулов по event-loop'у. Один процесс может иметь несколько loop'ов
# (тесты, юпитер) — пул, созданный в одном loop, нельзя использовать в другом
# (asyncpg/redis-py привязывают Future к loop на момент создания). Поэтому
# индексируем по id(loop). Слабая привязка — loop переиспользуется до закрытия,
# id стабилен на его время жизни.
_pools: dict[int, ArqRedis] = {}
_lock = asyncio.Lock()


async def get_arq_pool() -> ArqRedis:
    """Вернуть singleton ``ArqRedis``-пул для текущего event-loop'а.

    Конфигурация Redis читается из ENV ``REDIS_URL`` (дефолт
    ``redis://localhost:6379/0``) — та же логика, что в воркере.

    Returns:
        Готовый ``ArqRedis``, на котором можно вызывать
        ``enqueue_job(name, *args, **kwargs)``.
    """
    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    cached = _pools.get(loop_id)
    if cached is not None:
        return cached

    async with _lock:
        # Двойная проверка: пока ждали lock, другой coroutine мог успеть создать пул.
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
    """Закрыть все кэшированные пулы (для shutdown FastAPI / fixture-teardown)."""
    pools = list(_pools.values())
    _pools.clear()
    for pool in pools:
        await pool.close(close_connection_pool=True)
