"""Тонкий async-клиент к email-service ``POST /email/send`` (Phase 12.0).

billing-service использует email-service как fan-out для transactional
писем (``payment_succeeded`` / ``payment_failed``). Идемпотентность
обеспечивается на стороне email-service через ``idempotency_key``
(уникальный stripe_event_id). Дубль повторно не шлётся.

Если email-service недоступен или вернул 5xx — мы НЕ ретраим в hot-path
webhook handler (это блокирует ответ Stripe и приведёт к Stripe-retry,
который мы и так умеем обрабатывать). Логируем warning и завершаемся
успешно — следующий Stripe-retry пере-доставит event, и мы попытаемся
отправить email снова (тот же idempotency_key — email-service дедупит).
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from billing_service.config import Settings

_LOG: Final = logging.getLogger(__name__)
_TIMEOUT_SECONDS: Final = 5.0


async def send_email_async(settings: Settings, payload: dict[str, Any]) -> None:
    """POST к email-service /email/send. Best-effort: 5xx логируется, не raise.

    Параметры:
        settings: billing-service Settings (нужен ``email_service_url``).
        payload: тело запроса согласно email-service /email/send schema.

    Raises:
        Не raise'ит — best-effort delivery с дедупом на стороне email-service.
        Любая network-ошибка / non-2xx логируется как warning.
    """
    base = settings.email_service_url.rstrip("/")
    if not base:
        return
    url = f"{base}/email/send"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            _LOG.warning(
                "email-service returned %s for kind=%s idempotency_key=%s body=%s",
                response.status_code,
                payload.get("kind"),
                payload.get("idempotency_key"),
                response.text[:200],
            )
    except httpx.HTTPError as exc:
        _LOG.warning(
            "email-service request failed for kind=%s idempotency_key=%s: %s",
            payload.get("kind"),
            payload.get("idempotency_key"),
            exc,
        )


__all__ = ["send_email_async"]
