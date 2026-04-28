"""NotificationPreference — per-user toggle для notification event_type'ов.

Phase 8.0 wire-up (ADR-0029). Если строки нет → дефолты (enabled=True,
все известные channels). Это «opt-out» модель.

`user_id` — ``BigInteger`` без FK на ``users`` (которой пока нет, см.
``Notification`` модуль). Когда auth появится — добавим FK миграцией.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import TimestampMixin


class NotificationPreference(TimestampMixin, Base):
    """Настройка нотификаций пользователя для одного event_type.

    PK — composite ``(user_id, event_type)``: один пользователь одну
    настройку на тип события. Это упрощает upsert
    (``ON CONFLICT (user_id, event_type) DO UPDATE``) — Phase 8.x
    может расширить, если понадобятся дополнительные scope'ы (per-tree).

    Атрибуты:
        user_id: Получатель.
        event_type: Тип события из ``NotificationEventType`` (хранится
            text — валидация в API/dispatcher).
        enabled: ``False`` — dispatcher делает ранний возврат без
            создания row в ``notifications``. См. ADR-0029.
        channels: Список каналов, через которые user согласен получать.
            Запросы dispatch с каналом не из списка тихо его пропустят
            (запишут в ``channels_attempted`` как ``skipped: user_pref``).
            Пустой список == «не доставлять никуда» (эквивалент
            ``enabled=False`` для практических целей, но семантически
            «настроено явно»).
    """

    __tablename__ = "notification_preferences"

    # Composite PK через primary_key=True на двух колонках.
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    channels: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: ["in_app", "log"],
    )

    def __repr__(self) -> str:  # pragma: no cover — debug helper
        return (
            f"NotificationPreference(user_id={self.user_id}, "
            f"event_type={self.event_type!r}, enabled={self.enabled}, "
            f"channels={self.channels!r})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Override base.to_dict для composite-PK (нет ``id`` колонки)."""
        return {
            "user_id": self.user_id,
            "event_type": self.event_type,
            "enabled": self.enabled,
            "channels": list(self.channels),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = ["NotificationPreference"]
