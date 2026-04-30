"""Минимальный async-cache wrapper для dna-service (Phase 6.4).

Зачем не подключать ``redis.asyncio.Redis`` напрямую: dna-service пока не
использует Redis (queues живут в parser-service, FS OAuth state — там же).
Phase 6.4 добавляет ровно один вариант использования — cache compute-heavy
``GET /trees/{id}/triangulation`` на 1 час. Делать для этого
полноценную lifespan-инфру (init/dispose connection pool) — overkill;
делаем всё через FastAPI-зависимость, которую тесты подменяют на in-memory
fake. Когда появится второй consumer (Phase 6.5+) — экстрактнем общий
``app.state.redis`` и lifespan-init.

API:

* :class:`CacheBackend` — Protocol с ``get/setex``. Совместим с
  ``redis.asyncio.Redis`` и in-memory тестовым стабом.
* :func:`get_cache` — FastAPI-зависимость; возвращает либо реальный
  ``redis.asyncio.Redis`` (если ``DNA_SERVICE_REDIS_URL`` задан), либо
  no-op стаб (cache-miss всегда).

См. ADR-0054 §«Caching strategy» — почему 1 час, почему ключ включает
параметры запроса.
"""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from fastapi import Depends

from dna_service.config import Settings, get_settings


@runtime_checkable
class CacheBackend(Protocol):
    """Минимальный async-cache contract: ``get`` + ``setex``.

    Совместим с ``redis.asyncio.Redis`` и тестовыми in-memory заглушками.
    Намеренно НЕ требует ``delete``/``getdel`` — endpoint Phase 6.4
    использует только TTL-based eviction.
    """

    async def get(self, key: str) -> bytes | str | None: ...
    async def setex(self, key: str, ttl_seconds: int, value: str) -> object: ...


class _NullCache:
    """No-op cache для случая когда redis_url не сконфигурирован.

    Все ``get`` возвращают ``None`` (cache miss), все ``setex`` — silent.
    Endpoint в этом режиме всегда compute'ит свежие группы.
    """

    async def get(self, key: str) -> None:  # noqa: ARG002 — Protocol-compat stub
        return None

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        # Сигнатура совпадает с redis-py; параметры намеренно
        # игнорируются для no-op стаба.
        del key, ttl_seconds, value


_NULL_CACHE_SINGLETON: CacheBackend = _NullCache()


def get_cache(settings: Annotated[Settings, Depends(get_settings)]) -> CacheBackend:
    """Возвращает Redis-клиент или no-op стаб в зависимости от настроек.

    Не управляет lifecycle: ``redis.asyncio.Redis.from_url`` создаёт
    connection pool по требованию; для одного per-process'а pool'а это
    приемлемо в Phase 6.4 (low traffic, compute-on-demand). Когда
    появится второй consumer, перейдём на app.state-storage.
    """
    if not settings.redis_url:
        return _NULL_CACHE_SINGLETON
    # Импорт лениво, чтобы no-redis окружения (CI minimal) не падали.
    from redis.asyncio import Redis  # noqa: PLC0415 — lazy import by design

    client: CacheBackend = Redis.from_url(settings.redis_url, decode_responses=True)
    return client


__all__ = [
    "CacheBackend",
    "get_cache",
]
