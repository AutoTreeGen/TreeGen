"""SSE-эндпоинт live-прогресса bulk hypothesis-compute job (Phase 7.5 finalize).

``GET /trees/{tree_id}/hypotheses/compute-jobs/{job_id}/events`` —
Server-Sent Events стрим, который подписывается на тот же Redis
pubsub-канал ``job-events:{job_id}``, что использует ``run_bulk_hypothesis_job``
arq-worker. Контракт зеркалит ``imports_sse``: один канал на job, JSON-frame
на событие, закрытие на терминальной стадии.

Терминальные стадии bulk-compute: ``succeeded`` / ``failed`` / ``cancelled``
(см. ``services.bulk_hypothesis_runner.STAGE_*``). Промежуточные стадии —
``loading_rules`` / ``iterating_pairs`` / ``persisting`` — не закрывают
стрим, UI показывает их в progress-панели.

Дублирование с ``imports_sse`` минимальное и осознанное: набор стадий
у двух job-доменов не пересекается, и общий супер-frozenset с
``import_status`` ∪ ``hypothesis_status`` будет вводить в заблуждение
(close-логика на терминалке зависит от того, какой job стримим).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from shared_models.orm import HypothesisComputeJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.services.bulk_hypothesis_runner import (
    STAGE_CANCELLED,
    STAGE_FAILED,
    STAGE_SUCCEEDED,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Pubsub channel naming — единый формат с ``imports_sse`` и worker'ом.
# Использование одного namespace ``job-events:`` намеренное: worker
# публикует одинаково для import / bulk-compute, отличаются только
# наборы стадий внутри JSON. SSE-эндпоинт фильтрует по job_id-сегменту
# в URL'е, поэтому конфликтов между job-типами не бывает.
_CHANNEL_TEMPLATE = "job-events:{job_id}"

# Терминальные стадии bulk-compute. На любой из них SSE-стрим закрывается.
_TERMINAL_STAGES: frozenset[str] = frozenset({STAGE_SUCCEEDED, STAGE_FAILED, STAGE_CANCELLED})


def channel_name(job_id: uuid.UUID | str) -> str:
    """Имя pubsub-канала для конкретного bulk-compute job. Экспорт для тестов."""
    return _CHANNEL_TEMPLATE.format(job_id=job_id)


# Хук для тестов: подменяет фабрику Redis-клиента на ``fakeredis``.
# Зеркалит ``imports_sse._redis_client_factory`` — функция-уровня
# binding (не Depends) проще mock'ается через monkeypatch внутри
# EventSourceResponse-генератора.
_redis_client_factory: Any = None


def _make_redis_client(settings: Settings) -> redis_asyncio.Redis:
    """Создать async Redis-клиент. Тестовый хук — _redis_client_factory."""
    if _redis_client_factory is not None:
        client: redis_asyncio.Redis = _redis_client_factory()
        return client
    return redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)


def _is_terminal_event(payload: dict[str, Any]) -> bool:
    """Признак финального события: stage ∈ {succeeded, failed, cancelled}."""
    stage = payload.get("stage")
    return isinstance(stage, str) and stage in _TERMINAL_STAGES


async def _stream_events(
    request: Request,
    job_id: uuid.UUID,
    settings: Settings,
) -> AsyncIterator[dict[str, str]]:
    """Подписаться на ``job-events:{job_id}`` и форвардить сообщения.

    Логика повторяет ``imports_sse._stream_events`` — отличается только
    набором терминальных стадий. Heartbeat (``ping=15``) добавляется
    sse-starlette автоматически.
    """
    client = _make_redis_client(settings)
    pubsub = client.pubsub()
    chan = channel_name(job_id)
    await pubsub.subscribe(chan)
    try:
        while True:
            if await request.is_disconnected():
                break

            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=15.0,
            )
            if message is None:
                continue

            data = message.get("data")
            if not isinstance(data, str):
                continue

            yield {"data": data}

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
    "/trees/{tree_id}/hypotheses/compute-jobs/{job_id}/events",
    summary="SSE-стрим прогресса bulk hypothesis-compute job",
    response_class=EventSourceResponse,
    tags=["hypotheses", "bulk-compute", "sse"],
)
async def stream_compute_job_events(
    tree_id: uuid.UUID,
    job_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> EventSourceResponse:
    """Открыть SSE-соединение для конкретного bulk-compute job'а.

    Возвращает 404 если job не существует или принадлежит другому tree
    (no info-leak — параллель GET /compute-jobs/{id}). На существующий
    job открывается соединение, даже если он уже терминален: UI получит
    ноль live-событий и закроет стрим (это удобнее, чем 410, для
    reconnect-логики на фронте).
    """
    res = await session.execute(
        select(HypothesisComputeJob).where(HypothesisComputeJob.id == job_id)
    )
    job = res.scalar_one_or_none()
    if job is None or job.tree_id != tree_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Compute job {job_id} not found in tree {tree_id}",
        )

    return EventSourceResponse(
        _stream_events(request, job_id, settings),
        ping=15,
    )
