"""Redis async-клиент с тестовым хуком (тот же паттерн, что в parser-service).

В тестах ``_redis_client_factory`` подменяется на фабрику ``fakeredis``,
чтобы не нужно было поднимать реальный Redis.
"""

from __future__ import annotations

from typing import Any

import redis.asyncio as redis_asyncio

from archive_service.config import Settings

# Test-only фабрика. Production-коду присваивать НЕ нужно — он остаётся
# ``None``, и тогда работает обычный ``Redis.from_url``.
_redis_client_factory: Any = None


def make_redis_client(settings: Settings) -> redis_asyncio.Redis:
    """Создать async Redis-клиент (или fake — если тест подменил фабрику)."""
    if _redis_client_factory is not None:
        client: redis_asyncio.Redis = _redis_client_factory()
        return client
    return redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)
