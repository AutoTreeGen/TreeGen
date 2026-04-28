"""Progress-publisher для long-running job'ов через Redis pub/sub.

Используется ``import_runner`` (Phase 3.5) и в перспективе
``hypothesis_runner`` чтобы стримить промежуточные этапы
(parsing → entities → events → …) на канал Redis. API-gateway
поднимает SSE-эндпоинт, подписан на тот же канал, и пересылает
события в браузер (``EventSource`` на фронте).

Если ``redis`` is ``None`` — publisher становится no-op. Это позволяет
существующим синхронным caller'ам ``run_import`` (POST /imports)
работать без зависимости от Redis: они просто не передают publisher.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Protocol


class Stage(StrEnum):
    """Стадии импортного pipeline.

    Порядок отражает реальный поток в ``import_runner.run_import``:
    сначала парсинг файла, затем bulk-insert по группам сущностей,
    в конце — финализация (audit-row + commit).
    """

    PARSING = "parsing"
    ENTITIES = "entities"
    EVENTS = "events"
    PLACES = "places"
    SOURCES = "sources"
    MULTIMEDIA = "multimedia"
    FINALIZING = "finalizing"


class _RedisPublishProtocol(Protocol):
    """Минимальный интерфейс async-клиента Redis: метод ``publish``.

    Совместимо с ``redis.asyncio.Redis`` и ``arq.connections.ArqRedis``,
    а также с fakeredis-аналогами в тестах. Возвращаемое значение
    клиента (число подписчиков) нам не интересно.
    """

    async def publish(  # pragma: no cover - structural protocol
        self, channel: str, message: str
    ) -> Any: ...


class ProgressPublisher:
    """Публикация JSON-событий прогресса в Redis pub/sub.

    Каждое событие — ``{"stage": str, "current": int, "total": int,
    "message": str | None}`` (последнее поле опускается, если None).
    No-op, если ``redis`` is ``None``.
    """

    def __init__(self, redis: _RedisPublishProtocol | None, channel: str) -> None:
        self._redis = redis
        self._channel = channel

    @property
    def channel(self) -> str:
        """Имя Redis pub/sub канала, на который публикуются события."""
        return self._channel

    @property
    def is_enabled(self) -> bool:
        """True, если события реально пишутся в Redis."""
        return self._redis is not None

    async def publish(
        self,
        stage: Stage | str,
        current: int,
        total: int,
        message: str | None = None,
    ) -> None:
        """Опубликовать одно progress-событие. No-op если redis=None.

        Args:
            stage: Текущая стадия из enum ``Stage`` (или совместимая строка).
            current: Сколько единиц обработано.
            total: Сколько единиц всего планируется обработать.
            message: Опциональный человекочитаемый комментарий.
        """
        if self._redis is None:
            return
        stage_value = stage.value if isinstance(stage, Stage) else stage
        payload: dict[str, Any] = {
            "stage": stage_value,
            "current": current,
            "total": total,
        }
        if message is not None:
            payload["message"] = message
        await self._redis.publish(self._channel, json.dumps(payload))
