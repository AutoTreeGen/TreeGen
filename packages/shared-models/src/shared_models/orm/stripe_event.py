"""StripeEvent — лог обработанных Stripe webhook events (idempotency).

Stripe гарантирует **at-least-once** доставку webhook'ов: тот же event_id
(``evt_*``) может прийти несколько раз — при retry на сетевой timeout,
при manual replay из dashboard. Мы должны обработать каждый event ровно
один раз. Это решается через unique constraint на ``stripe_event_id``:
обработчик пытается INSERT, IntegrityError → дубль, возврат 200 OK без
повторного применения.

Phase 12.x может удалить старые ``processed`` events (TTL 90 дней) —
дубли в окне Stripe retry-window (≤72 часа) гарантированно отлавливаются,
а history дольше нам не нужна (audit-log в БД и Stripe Dashboard
покрывают долгосрочные нужды).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import StripeEventStatus
from shared_models.mixins import IdMixin, TimestampMixin


class StripeEvent(IdMixin, TimestampMixin, Base):
    """Запись об обработанном (или попытке обработки) webhook event.

    Атрибуты:
        id: Internal UUIDv7 PK.
        stripe_event_id: ``evt_*`` от Stripe. Уникален → idempotency.
        event_type: ``checkout.session.completed`` / ``customer.subscription.updated``
            и т.п. Сохраняем как-есть, без enum-валидации — Stripe иногда
            добавляет новые события без deprecation, и мы не хотим 500'ить
            на новой строке.
        status: ``RECEIVED`` / ``PROCESSED`` / ``FAILED`` (см. enum docstring).
        payload: Сырое JSON-тело event'а от Stripe (после signature-verification).
            Хранится для debugging и ручного re-process'а через скрипт,
            если обработчик упал. Stripe также отдаёт сырое тело через
            Dashboard, но локальный snapshot быстрее.
        error_message: Текст exception, если ``status=FAILED``. NULL иначе.
    """

    __tablename__ = "stripe_events"

    stripe_event_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=StripeEventStatus.RECEIVED.value,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)


__all__ = ["StripeEvent"]
