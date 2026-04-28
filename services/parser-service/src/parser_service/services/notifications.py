"""Notification dispatch — enqueue arq job (Phase 8.0 wire-up, ADR-0029).

Контракт (зеркалит ADR-0029):

* :func:`notify_hypothesis_pending_review` ставит arq-job в очередь
  ``imports`` (общая arq-очередь Phase 3.5). Job-функция —
  :func:`parser_service.worker.dispatch_notification_job` — внутри
  своего процесса делает HTTP POST в notification-service. Это:

  - Снимает сетевой хвост с транзакции hypothesis_runner (раньше
    ждали httpx внутри commit).
  - Даёт нам бесплатный backoff/retry от arq.
  - Делает потерю нотификации видимой (failed job в arq), а не
    тихо проглоченной.

* :func:`post_notify_request` — низкоуровневый HTTP POST.
  Экспортируется отдельно: его вызывает worker. ``base_url``-логика
  (env var + light-integration mode) живёт здесь, чтобы оба пути
  (старый sync, новый async) видели одинаковый «notification-service
  отключён → no-op».

Cross-service deps: знаем только URL и form. Не импортируем
``notification_service.*`` чтобы избежать import-cycle.
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
_TIMEOUT_SEC = 5.0


def _resolved_base_url(override: str | None) -> str:
    """Разрешить base_url из override / env / пустоты.

    Пустая строка означает «notification-service намеренно отключён»
    (например, в локальной разработке без поднятой второй FastAPI).
    Caller'ы при пустом URL должны делать early-return.
    """
    if override is not None:
        return override
    return _DEFAULT_URL


async def post_notify_request(
    payload: dict[str, Any],
    *,
    base_url: str | None = None,
) -> bool:
    """POST ``payload`` в ``{base_url}/notify`` notification-service'а.

    Используется из worker job — синхронно ждёт ответ, чтобы arq мог
    решить «успех/ретрай» по возвращаемому boolean / исключению.

    Returns:
        ``True`` — 2xx (нотификация принята или дедуплицирована).
        ``False`` — 4xx (валидационная ошибка — ретраи бесполезны).

    Raises:
        httpx.HTTPError: Сеть/таймаут/5xx — arq ретрайнет.
    """
    url = _resolved_base_url(base_url)
    if not url:
        # Намеренно молча — light integration mode; «success», чтобы
        # worker-job не маркировался failed когда сервис отключён.
        return True

    async with httpx.AsyncClient(timeout=_TIMEOUT_SEC) as client:
        response = await client.post(f"{url.rstrip('/')}/notify", json=payload)

    if response.status_code >= 500:
        # arq возьмёт на ретрай через raise.
        response.raise_for_status()
    if response.status_code >= 400:
        # 4xx — навсегда плохой payload, ретрай не поможет. Лог + не-успех.
        _LOG.warning(
            "notification-service rejected payload (%s): %s",
            response.status_code,
            response.text[:200],
        )
        return False
    return True


async def notify_hypothesis_pending_review(
    *,
    user_id: uuid.UUID,
    hypothesis_id: uuid.UUID,
    tree_id: uuid.UUID,
    composite_score: float,
    hypothesis_type: str,
) -> None:
    """Уведомить tree-owner'а о новой ``hypothesis_pending_review`` гипотезе.

    Asynchronous: ставит arq-job в очередь и сразу возвращает. Сама
    доставка (HTTP POST в notification-service) исполняется воркером.

    Любая ошибка enqueue (Redis unreachable etc.) → log warning + return.
    Это сознательно — notification — best-effort, доменная транзакция
    не должна откатываться из-за инфраструктуры нотификаций.

    Args:
        user_id: Tree owner — преобразуется в int через ``UUID.int``
            для совместимости со схемой ``NotifyRequest`` (см.
            notification-service schemas).
        hypothesis_id, tree_id: для UI deep-link.
        composite_score: для приоритизации в UI.
        hypothesis_type: shape-key для шаблона сообщения.
    """
    if not _DEFAULT_URL:
        # Light-integration mode: notification-service намеренно
        # отключён (env var не задана). Раньше это давало silent no-op
        # синхронного httpx-POST'а; сохраняем то же поведение для
        # async-пути — иначе в тестах/dev мы будем без причины
        # давить Redis enqueue'ами, которые worker всё равно проигнорирует.
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

    # Lazy import: parser_service.queue → worker.py → этот модуль.
    # Ленивый import рвёт цикл и держит test-fixture (override
    # get_arq_pool через AsyncMock) рабочим без особых трюков.
    try:
        from parser_service.queue import get_arq_pool  # noqa: PLC0415 — циклоразрыв

        pool = await get_arq_pool()
        await pool.enqueue_job("dispatch_notification_job", payload)
    except Exception as exc:
        # Best-effort: notification — не доменная транзакция,
        # инфраструктурный сбой не должен пробрасываться вверх.
        _LOG.warning(
            "failed to enqueue notification for hypothesis %s: %s",
            hypothesis_id,
            exc,
        )


__all__ = [
    "notify_hypothesis_pending_review",
    "post_notify_request",
]
