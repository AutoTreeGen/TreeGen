"""Lightweight client to ``notification-service`` (Phase 4.9 / 8.0).

Fire-and-forget POST ``/notify`` — never блокирует основной flow. Любая
ошибка (network, 4xx, 5xx, validation) → warning в лог + ``None`` возврат.

Используется ``hypothesis_runner`` для оповещения tree-owner'а о новой
``pending_review`` гипотезе.

Cross-service deps: знаем только URL и form. Не импортируем
``notification_service.*`` чтобы избежать import-cycle между сервисами;
contract-test (Phase 8.x) гарантирует что request shape совпадает.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx

_LOG = logging.getLogger(__name__)

# Env var: единый namespace AUTOTREEGEN_NOTIFICATION_SERVICE_URL.
# Если не задан — нотификации просто не отправляются (light integration).
_DEFAULT_URL = os.environ.get(
    "AUTOTREEGEN_NOTIFICATION_SERVICE_URL",
    "",
)
_TIMEOUT_SEC = 2.0


async def notify_hypothesis_pending_review(
    *,
    user_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    tree_id: uuid.UUID,
    composite_score: float,
    hypothesis_type: str,
    base_url: str | None = None,
) -> None:
    """Уведомить tree-owner'а о новой ``hypothesis_pending_review``.

    Любая ошибка → log warning + return. Нет re-raise. Нет retry —
    delivery guarantees лежат на notification-service (idempotency-окно
    + retry queue, Phase 8.1+).

    Args:
        user_id: Tree owner — преобразуется в int через ``UUID.int``
            (128-битный) для совместимости со схемой ``NotifyRequest``.
            Если notification-service хранит user_id в `int` колонке
            меньшей ширины, придёт 422 — graceful skip.
        hypothesis_id, tree_id: для UI deep-link.
        composite_score: для приоритизации в UI («показать сначала >0.8»).
        hypothesis_type: shape-key для шаблона сообщения.
        base_url: Override для тестов / dev'а. ``None`` → env var.
            Пустая строка → no-op без warning'а (notification-service
            намеренно отключён).
    """
    url = base_url if base_url is not None else _DEFAULT_URL
    if not url:
        # Намеренно молча — light integration mode.
        return

    payload: dict[str, Any] = {
        "user_id": user_id.int,
        "event_type": "hypothesis_pending_review",
        "payload": {
            "hypothesis_id": str(hypothesis_id),
            "tree_id": str(tree_id),
            "composite_score": composite_score,
            "hypothesis_type": hypothesis_type,
            # ref_id для idempotency-окна notification-service'а.
            "ref_id": str(hypothesis_id),
        },
        "channels": ["in_app", "log"],
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
            response = await client.post(f"{url.rstrip('/')}/notify", json=payload)
        if response.status_code >= 400:
            _LOG.warning(
                "notification-service POST /notify returned %s for hypothesis %s: %s",
                response.status_code,
                hypothesis_id,
                response.text[:200],
            )
            return
    except httpx.HTTPError as exc:
        _LOG.warning(
            "notification-service unreachable for hypothesis %s: %s",
            hypothesis_id,
            exc,
        )
        return
