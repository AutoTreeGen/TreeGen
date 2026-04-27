"""InAppChannel — отметить нотификацию как доступную для просмотра в UI.

Сама запись в `notifications` уже создана dispatcher'ом до вызова
канала, так что in-app доставка — это no-op «доставлено» (запись
уже доступна через ``GET /users/me/notifications``). Класс
существует для симметрии с другими каналами и для возможной
будущей логики (push в WebSocket-stream и т. п.).
"""

from __future__ import annotations

from shared_models.orm import Notification


class InAppChannel:
    """Доставка через запись в БД (фронт читает через GET endpoint)."""

    # Не Final — Channel Protocol ожидает settable атрибут (PEP 544).
    name: str = "in_app"

    async def send(self, notification: Notification) -> bool:
        """Зарегистрировать нотификацию как in-app delivered.

        Сейчас — no-op-успех: запись уже в БД через dispatcher,
        UI будет видеть её как unread в шапке. В Phase 8.3, когда
        появится WebSocket, здесь же будем push'ить событие в открытое
        соединение пользователя.
        """
        # Используем `notification` чтобы не было unused-arg warning.
        _ = notification.id
        return True
