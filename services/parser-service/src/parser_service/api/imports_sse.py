"""SSE-эндпоинт live-прогресса импорта (Phase 3.5).

``GET /imports/{import_job_id}/events`` — Server-Sent Events стрим,
который подписывается на Redis pubsub-канал ``job-events:{job_id}`` и
форвардит каждое событие клиенту в формате ``data: {json}\\n\\n``.

Соединение закрывается на терминальной стадии (``succeeded`` /
``failed`` / ``cancelled``). Heartbeat каждые 15 секунд (см.
``sse-starlette`` ``ping=15``) — чтобы прокси/балансировщик не убивал
idle TCP. Если клиент дисконнектится первым — ``EventSourceResponse``
ловит ``CancelledError`` и закрывает Redis-подписку.

Зависимости:

* ``sse-starlette>=2.0`` — корректная сериализация событий + auto-ping.
* ``redis[hiredis]>=5.0`` — async pubsub-клиент.

Worker (см. ``feat/phase-3.5-arq-worker``) публикует события в тот же
канал через ``redis.publish("job-events:{job_id}", json.dumps(event))``.
SSE-эндпоинт отвечает за:

1. Snapshot из БД на старте (если worker уже опубликовал что-то ДО
   подключения SSE — первый ``progress`` снапшот UI всё равно увидит,
   но по pull через ``GET /imports/{id}``; тут шлём только live-стрим).
2. Live-форвардинг pubsub-сообщений.
3. Закрытие на терминальной стадии.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from shared_models.enums import ImportJobStatus
from shared_models.orm import ImportJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from parser_service.config import Settings, get_settings
from parser_service.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter()

# Pubsub channel naming — синхронно с worker'ом из зависимого PR.
# Один канал на job — позволяет worker'у не вести список клиентов и
# даёт SSE-эндпоинту пропустить сообщения других job'ов.
_CHANNEL_TEMPLATE = "job-events:{job_id}"

# Терминальные стадии, на которых SSE-стрим закрывается. Источник
# истины — ImportJobStatus, но проверяем по ``stage``-полю события
# (worker публикует ``stage=succeeded|failed|cancelled`` как маркер
# финала, в дополнение к промежуточным стадиям parsing/persons/...).
_TERMINAL_STAGES: frozenset[str] = frozenset(
    {
        ImportJobStatus.SUCCEEDED.value,
        ImportJobStatus.FAILED.value,
        ImportJobStatus.CANCELLED.value,
        # PARTIAL = частично импортировано (worker вышел с error на
        # одной из поздних стадий; SUCCEEDED-данные уже коммитнуты).
        # Для UI это тоже терминал.
        ImportJobStatus.PARTIAL.value,
    }
)


def channel_name(job_id: uuid.UUID | str) -> str:
    """Имя pubsub-канала для конкретного job.

    Экспортируется для тестов: они публикуют в этот же канал, чтобы
    проверить, что SSE-эндпоинт форвардит сообщение клиенту.
    """
    return _CHANNEL_TEMPLATE.format(job_id=job_id)


# Хук для тестов: pytest подменяет фабрику, чтобы вернуть ``fakeredis``
# клиент вместо реального Redis. Функция-уровня (а не Depends) специально:
# подмена dependency_overrides внутри ``EventSourceResponse``-генератора
# работает капризно из-за того, что генератор живёт за пределами
# request-scope. Module-level binding мокается через monkeypatch чисто.
_redis_client_factory: Any = None


def _make_redis_client(settings: Settings) -> redis_asyncio.Redis:
    """Создать async Redis-клиент. Тестовый хук — подменить _redis_client_factory."""
    if _redis_client_factory is not None:
        client: redis_asyncio.Redis = _redis_client_factory()
        return client
    return redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)


def _is_terminal_event(payload: dict[str, Any]) -> bool:
    """Признак, что событие финальное и SSE-стрим можно закрывать.

    Воркер публикует ``stage`` для всех событий; на финале — стадия,
    совпадающая с ``ImportJobStatus`` (``succeeded`` / ``failed`` / ...).
    """
    stage = payload.get("stage")
    return isinstance(stage, str) and stage in _TERMINAL_STAGES


async def _stream_events(
    request: Request,
    job_id: uuid.UUID,
    settings: Settings,
) -> AsyncIterator[dict[str, str]]:
    """Подписаться на ``job-events:{job_id}`` и форвардить сообщения.

    Каждое сообщение — словарь с ключом ``data`` (JSON-строка). Это
    формат, который ``EventSourceResponse`` сериализует в
    ``data: <value>\\n\\n``. Heartbeat (``ping=15`` секунд) добавляет
    sse-starlette автоматически.
    """
    client = _make_redis_client(settings)
    pubsub = client.pubsub()
    chan = channel_name(job_id)
    await pubsub.subscribe(chan)
    try:
        while True:
            # Если клиент уже отвалился — sse-starlette кинет
            # CancelledError при следующем yield. Дополнительная
            # явная проверка — для случая когда нет новых
            # сообщений, но мы хотим выйти после disconnect.
            if await request.is_disconnected():
                break

            # ``timeout=15`` секунд: если за это время нет сообщения,
            # get_message вернёт None — мы крутим цикл и проверяем
            # disconnect. sse-starlette сам шлёт ping-comment, не
            # дублируем его здесь.
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=15.0,
            )
            if message is None:
                continue

            data = message.get("data")
            if not isinstance(data, str):
                # Бинарные/неожиданные сообщения — пропускаем.
                continue

            yield {"data": data}

            # Если worker опубликовал терминальное событие —
            # закрываем стрим, не ждём ещё одного round-trip.
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                logger.warning("non-json sse payload on %s: %r", chan, data[:200])
                continue
            if _is_terminal_event(payload):
                break
    finally:
        try:
            await pubsub.unsubscribe(chan)
            await pubsub.aclose()
        except Exception:
            logger.exception("failed to close pubsub for %s", chan)
        try:
            await client.aclose()
        except Exception:
            logger.exception("failed to close redis client for %s", chan)


@router.get(
    "/{import_job_id}/events",
    summary="SSE-стрим прогресса импорта",
    response_class=EventSourceResponse,
)
async def stream_import_events(
    import_job_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventSourceResponse:
    """Открыть SSE-соединение и форвардить события из Redis pubsub.

    Возвращает 404 если ``import_job_id`` не существует. На существующий
    job открывается соединение, даже если он уже терминален — UI получит
    ноль live-событий и закроет стрим (контракт удобнее, чем 410: легче
    тестировать и реализовывать reconnect). Текущий снапшот UI получает
    отдельно через ``GET /imports/{id}``.
    """
    # Существование проверяем заранее: SSE-генератор не может вернуть
    # 404 после того, как заголовки уже улетели клиенту.
    res = await session.execute(select(ImportJob).where(ImportJob.id == import_job_id))
    job = res.scalar_one_or_none()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import job {import_job_id} not found",
        )

    return EventSourceResponse(
        _stream_events(request, import_job_id, settings),
        ping=15,
    )
