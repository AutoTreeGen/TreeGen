"""Тонкая обёртка над Resend HTTP API (Phase 12.2, ADR-0039).

Resend SDK для Python существует, но добавляет одну зависимость
поверх httpx без значимой выгоды для нашего use-case (один POST
endpoint). Вместо SDK — прямой ``httpx.AsyncClient``, что даёт:

* Полный контроль над timeout / retry — без сюрпризов от SDK,
  который под капотом использует sync requests или скрывает retry.
* Простой mock для тестов через ``httpx.MockTransport`` без
  patching SDK internals (см. ADR-0039 §«Testing»).
* Один API-call на отправку, поэтому SDK overkill.

Resend API: ``POST https://api.resend.com/emails`` с Bearer auth.
Возвращает ``{"id": "re_*"}``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

import httpx

from email_service.config import Settings

_LOG: Final = logging.getLogger(__name__)
_RESEND_BASE_URL: Final = "https://api.resend.com"


class ResendError(RuntimeError):
    """Resend API вернул ошибку или не отвечает."""


@dataclass(frozen=True)
class SendResult:
    """Успешный ответ Resend."""

    message_id: str


async def send_via_resend(
    settings: Settings,
    *,
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SendResult:
    """Отправить письмо через Resend API.

    Args:
        settings: ``email_service`` settings (для api_key, from, timeout).
        to: Email-адрес получателя.
        subject: Тема. Уже отрендерена шаблоном.
        html_body: HTML-версия. Уже отрендерена.
        text_body: Plain text версия. Уже отрендерена.
        transport: Override для тестов (``httpx.MockTransport``).

    Returns:
        ``SendResult`` с provider message_id.

    Raises:
        ResendError: HTTP-ошибка от Resend (4xx/5xx) или network failure.
    """
    if not settings.resend_api_key:
        msg = "EMAIL_SERVICE_RESEND_API_KEY is not configured"
        raise ResendError(msg)

    payload = {
        "from": settings.resend_from,
        "to": [to],
        "subject": subject,
        "html": html_body,
        "text": text_body,
    }
    headers = {
        "Authorization": f"Bearer {settings.resend_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(
        transport=transport,
        base_url=_RESEND_BASE_URL,
        timeout=settings.resend_timeout_seconds,
    ) as client:
        try:
            response = await client.post("/emails", json=payload, headers=headers)
        except httpx.HTTPError as exc:
            msg_0 = f"Resend network error: {exc}"
            raise ResendError(msg_0) from exc

    if response.status_code >= 400:
        # Resend кладёт error message в body при non-2xx; вытаскиваем
        # для логов и пробрасываем caller'у. Сам status code тоже
        # включаем — caller может различать transient (5xx) от
        # permanent (4xx).
        try:
            body_text = response.text
        except UnicodeDecodeError:
            body_text = "<binary>"
        msg = f"Resend rejected request: status={response.status_code} body={body_text}"
        raise ResendError(msg)

    body = response.json()
    message_id = body.get("id")
    if not isinstance(message_id, str) or not message_id:
        msg = f"Resend response missing id: {body!r}"
        raise ResendError(msg)
    return SendResult(message_id=message_id)


__all__ = ["ResendError", "SendResult", "send_via_resend"]
