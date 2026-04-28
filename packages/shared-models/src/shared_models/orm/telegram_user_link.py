"""TelegramUserLink — связь TreeGen-user'а с Telegram-чатом (Phase 14.0).

Service-table: per-user opt-in mapping `(user_id, tg_chat_id)`. Без
`tree_id`, `provenance`, `version_id`, `status`, `confidence_score` —
это user setting, не доменный факт. Pattern совпадает с
`NotificationPreference` (ADR-0029) и `EmailSendLog` (ADR-0039).

Privacy / GDPR (CLAUDE.md §3.5, ADR-0040 §«Account linking flow»):

* `tg_chat_id` хранится **только** после явного opt-in flow:
  `/start` в боте → user открывает one-time link на web → web
  подтверждает с Clerk-JWT → запись создаётся.
* Revocation = `revoked_at` timestamp, не tombstone-soft-delete.
  GDPR-erasure (Phase 13.x) — hard delete CASCADE'ом с user'а.
* `tg_user_id` хранится для audit-trail и confirmation-message'а
  при unlink'е; не используется для авторизации.

См. ADR-0040 для полного flow.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class TelegramUserLink(IdMixin, TimestampMixin, Base):
    """Привязка TreeGen-user'а к Telegram-чату.

    Атрибуты:
        id: UUIDv7 PK.
        user_id: FK на ``users.id``, CASCADE на удаление user'а
            (GDPR erasure).
        tg_chat_id: Telegram chat_id (int64). UNIQUE — один TG-чат
            не может быть привязан к двум user'ам.
        tg_user_id: Telegram user_id (int64) того, кто инициировал
            link. Не используется для авторизации, только для
            audit / unlink-message'а.
        linked_at: Timestamp подтверждения линка (web-у вернул 200).
        revoked_at: Timestamp отзыва (NULL = активная связь).
            Hard delete — через GDPR-erasure pipeline.
    """

    __tablename__ = "telegram_user_links"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tg_chat_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        unique=True,
        index=True,
    )
    tg_user_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    linked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tg_chat_id",
            name="uq_telegram_user_links_user_chat",
        ),
    )


__all__ = ["TelegramUserLink"]
