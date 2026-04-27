"""LogChannel — пишет ``logger.info`` для каждой нотификации.

Полезно для:
- Локального дебага (легко grep'ать в console);
- Audit-trail в проде (если структурированные логи отправляются в
  централизованный лог-агрегатор);
- Smoke-тестов «notification вообще создана».

Не зависит от внешних систем, поэтому ``send()`` практически не падает.
Если падает — обрабатывается dispatcher'ом как любой другой канал.
"""

from __future__ import annotations

import logging

from shared_models.orm import Notification

_LOG = logging.getLogger("notification_service.log_channel")


class LogChannel:
    """Доставка через структурированный logger.info()."""

    # Не Final — Channel Protocol ожидает settable атрибут (PEP 544).
    name: str = "log"

    async def send(self, notification: Notification) -> bool:
        """Эмитировать одну строку лога с ключевыми полями нотификации."""
        _LOG.info(
            "[notify] id=%s user=%s type=%s",
            notification.id,
            notification.user_id,
            notification.event_type,
        )
        return True
