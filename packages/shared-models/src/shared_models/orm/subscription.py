"""Subscription — Stripe billing state per user (Phase 12.0, ADR-0042).

Canonical source of truth для plan/status пользователя. Мутируется
**только** webhook'ами от Stripe (handlers в ``billing-service``);
application-side код может ТОЛЬКО читать. Любая прямая мутация в
обход webhook'а ломает eventual-consistency между нами и Stripe.

User → много row (исторически): один user может последовательно иметь
несколько Stripe subscription'ов (cancel → resubscribe = новый sub_id).
Уникальный constraint — на ``stripe_subscription_id``, не на ``user_id``.
Поиск активной подписки — по (user_id, status=ACTIVE), упорядоченный
по ``current_period_end DESC``.

Soft-delete отсутствует: ``CANCELED`` — это статус, не tombstone
(ADR-0042 §«Subscription lifecycle»). Запись остаётся как audit и
для отображения «подписка кончилась X».
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import Plan, SubscriptionStatus
from shared_models.mixins import IdMixin, TimestampMixin


class Subscription(IdMixin, TimestampMixin, Base):
    """Stripe subscription state (canonical, webhook-mutated).

    Атрибуты:
        id: UUIDv7 PK (наш internal).
        user_id: FK → ``users.id``. НЕ unique: один user может иметь
            несколько исторических подписок (cancel + resubscribe).
        stripe_subscription_id: ``sub_*`` от Stripe. Unique → idempotent
            upsert по этому ключу из webhook'ов.
        plan: ``Plan`` enum (FREE/PRO/PREMIUM). Хранится как text.
        status: ``SubscriptionStatus`` enum. Хранится как text.
        current_period_end: timestamp окончания оплаченного периода.
            Используется для grace-period после PAST_DUE
            (ADR-0042 §«Failed payment policy»).
        cancel_at_period_end: если True, Stripe автоматически переведёт
            в CANCELED после ``current_period_end``. UI показывает
            «активна до X, после — Free».
    """

    __tablename__ = "subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_subscription_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    plan: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=Plan.FREE.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SubscriptionStatus.ACTIVE.value,
    )
    current_period_end: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


__all__ = ["Subscription"]
