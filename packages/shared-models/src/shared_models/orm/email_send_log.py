"""EmailSendLog — лог попыток transactional-email отправки (Phase 12.2).

Идемпотентность: уникальный ``idempotency_key`` гарантирует, что
повторный POST /email/send с тем же ключом не приведёт ко второй
отправке. Ключ формируется caller'ом (например, ``stripe_event_id`` для
billing-событий, ``clerk_user_id+welcome`` для signup'а).

Privacy / GDPR (ADR-0039 §«Privacy»):

* ``params`` jsonb хранит **только non-PII** payload, который
  безопасно показывать в support tickets и audit-log:
  amounts, dates, locale, plan_name, tree_name (если шаблон требует).
* Email-адрес получателя берётся из ``users.email`` на send-time и
  **не сохраняется** в этой таблице — иначе stale-копии при изменении.
* DNA / health / биометрические данные **никогда** не попадают в
  ``params`` (см. CLAUDE.md §3.5 и ADR-0039 §«DNA hard rule»).
* Soft-delete отсутствует: каждая строка — immutable provider-side
  audit-record, удаляется только GDPR-erasure pipeline'ом полностью
  (CASCADE с user'а).

См. ADR-0039 §«Schema» для retention-политики (90-day TTL,
управляется отдельным pruning-скриптом — Phase 12.x).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import EmailSendStatus
from shared_models.mixins import IdMixin, TimestampMixin


class EmailSendLog(IdMixin, TimestampMixin, Base):
    """Запись попытки отправки transactional-email.

    Атрибуты:
        id: UUIDv7 (наш internal PK).
        idempotency_key: Caller-supplied строка (например,
            ``stripe_event_id``). UNIQUE — повторный POST с тем же
            ключом возвращает существующую запись без второй отправки.
        kind: Значение ``EmailKind`` enum (text-storage, см.
            ``shared_models.enums``).
        recipient_user_id: FK на ``users.id``. Email-адрес получаем
            из ``users.email`` на send-time, не дублируем здесь.
        status: ``EmailSendStatus`` enum.
        provider_message_id: ``re_*`` от Resend (или эквивалент).
            ``None`` для ``SKIPPED_OPTOUT`` / ``QUEUED``.
        error: Текст provider-ошибки при ``FAILED``. ``None`` иначе.
        params: Безопасный non-PII payload, переданный в шаблон.
            Audit-log смотрит сюда чтобы проверить, что именно ушло.
            ``redact_email_params(...)`` фильтрует на insert.
        sent_at: Timestamp успешной отправки. ``None`` пока ``status``
            не стал ``SENT``.
    """

    __tablename__ = "email_send_log"

    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EmailSendStatus.QUEUED.value,
    )
    provider_message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        default=None,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    sent_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


__all__ = ["EmailSendLog"]
