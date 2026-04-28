"""Channel Protocol (PEP 544).

Реализации не наследуются от ABC — типизация структурная, проверяется
только сигнатурой ``send()``. Это match'ит pattern из inference-engine
(см. ROADMAP §7).
"""

from __future__ import annotations

from typing import Protocol

from shared_models.orm import Notification


class Channel(Protocol):
    """Способ доставки нотификации.

    Реализация:

    1. Получает свежезаписанный (или uncommitted, если вызван внутри
       той же транзакции) :class:`Notification`.
    2. Отдаёт его в свой mechanism (БД-запись, лог, email, push, ...).
    3. Возвращает ``True`` при успешной доставке, ``False`` при сбое.
       Исключения, не пойманные внутри ``send()``, обрабатываются
       dispatcher'ом и записываются в ``channels_attempted`` как
       ``success=False, error=<message>``.

    ``name`` — стабильный идентификатор (``"in_app"``, ``"log"``,
    ``"email"`` …). Используется в ``channels_attempted`` и в
    ``NotifyRequest.channels``.
    """

    name: str

    async def send(self, notification: Notification) -> bool:
        """Доставить нотификацию.

        Args:
            notification: ORM-модель — уже добавленная в session, но
                необязательно committed. Channel может читать её поля
                (id, user_id, event_type, payload), но не должен мутировать
                ``delivered_at`` / ``read_at`` — это работа dispatcher'а.

        Returns:
            ``True`` если канал считает доставку успешной.
        """
        ...
