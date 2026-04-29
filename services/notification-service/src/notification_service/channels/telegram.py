"""TelegramChannel — push нотификации в telegram-bot service (Phase 14.1, ADR-0056).

Канал делает HTTP POST на ``{bot_url}/telegram/notify`` с
``X-Internal-Service-Token``. Bot сам резолвит TelegramUserLink, проверяет
``notifications_enabled`` и делает ``Bot.send_message`` через Telegram API.

User-id mismatch (pre-existing tech-debt, ADR-0024 §Контекст): Notification
ORM хранит ``user_id`` как ``BigInteger`` без FK на users. TelegramUserLink
ключуется UUID-ом ``users.id``. Bridge: caller включает
``payload["telegram_user_id"]`` (UUID-строка) при создании нотификации.
Если поля нет — channel skip'ает (delivered=False, reason=no_uuid_in_payload).

Failure modes:

* bot URL/token не сконфигурированы → ``send()`` возвращает False (skip);
  это нормальная ситуация в локальной dev-среде без bot'а.
* HTTP-ошибка на сторону bot'а → ``send()`` возвращает False; dispatcher
  записывает в ``channels_attempted`` как failure без exception'а.
* bot вернул 200 с ``delivered=False`` (нет линка / unsubscribed) →
  ``send()`` возвращает False; это не «ошибка», просто факт «канал не
  применим к этому user'у».

Format: для скелета ``[event_type] payload`` plain-text. Phase 14.x может
заменить на event-type-aware шаблоны (как email-service).
"""

from __future__ import annotations

import json
import logging

import httpx
from shared_models.orm import Notification

from notification_service.config import get_settings

_LOG = logging.getLogger("notification_service.telegram_channel")


class TelegramChannel:
    """Доставка через telegram-bot ``/telegram/notify`` (HTTP)."""

    name: str = "telegram"

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        """Inject ``http_client`` для тестов; production создаёт внутри ``send``."""
        self._http_client = http_client

    async def send(self, notification: Notification) -> bool:
        """Push сообщения в bot. Возвращает True iff bot ответил delivered=True."""
        settings = get_settings()
        if not settings.telegram_bot_url or not settings.telegram_internal_token:
            _LOG.debug(
                "telegram channel skipped — bot URL/token not configured (notification id=%s)",
                notification.id,
            )
            return False

        # Bridge int(Notification.user_id) → UUID(users.id) через
        # caller-supplied payload field. См. модульный docstring.
        payload_dict = notification.payload or {}
        tg_user_uuid = payload_dict.get("telegram_user_id")
        if not isinstance(tg_user_uuid, str) or not tg_user_uuid:
            _LOG.debug(
                "telegram channel skipped — no telegram_user_id (UUID) in payload "
                "(notification id=%s)",
                notification.id,
            )
            return False

        message = _format_message(notification)
        url = f"{settings.telegram_bot_url.rstrip('/')}/telegram/notify"
        headers = {
            "X-Internal-Service-Token": settings.telegram_internal_token,
            "Content-Type": "application/json",
        }
        payload = {
            "user_id": tg_user_uuid,
            "message": message,
        }

        client = self._http_client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=settings.telegram_request_timeout_seconds)
            owns_client = True

        try:
            response = await client.post(url, content=json.dumps(payload), headers=headers)
        except httpx.HTTPError as exc:
            _LOG.warning(
                "telegram channel HTTP error for notification id=%s: %s",
                notification.id,
                exc,
            )
            return False
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code != 200:
            _LOG.warning(
                "telegram channel got non-200 from bot for notification id=%s: %s %s",
                notification.id,
                response.status_code,
                response.text[:200],
            )
            return False

        try:
            body = response.json()
        except json.JSONDecodeError:
            return False
        delivered = bool(body.get("delivered"))
        if not delivered:
            _LOG.info(
                "telegram channel not delivered for notification id=%s reason=%s",
                notification.id,
                body.get("reason"),
            )
        return delivered


def _format_message(notification: Notification) -> str:
    """Format notification as plain-text Telegram message.

    Phase 14.1 — minimal: ``[event_type] {key=value, ...}``. Phase 14.x
    заменит на event-type-aware шаблоны (напр., через email-service-style
    templates).
    """
    event = notification.event_type
    payload_dict = notification.payload or {}
    if not payload_dict:
        return f"[{event}]"
    parts = [f"{k}={v}" for k, v in sorted(payload_dict.items())]
    body = ", ".join(parts)
    return f"[{event}] {body}"
