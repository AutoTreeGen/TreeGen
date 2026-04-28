"""StripeSubscription — активная (или недавно активная) подписка пользователя.

Phase 12.0 хранит **последнюю** подписку каждого user'а — даже после
cancel'а, чтобы UI мог показать «когда подписка кончилась». Если user
заведёт новую подписку, она затрёт старую запись (новый stripe_sub_id).
Полная история биллинга остаётся на стороне Stripe — мы не дублируем.

Один user → одна row (unique constraint на user_id). Если в будущем
понадобится поддержка нескольких параллельных подписок (team plans,
add-ons), это будет breaking change → миграция + ADR.
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


class StripeSubscription(IdMixin, TimestampMixin, Base):
    """Текущая (или последняя) Stripe-подписка пользователя.

    Атрибуты:
        id: Internal UUIDv7 PK.
        user_id: FK → ``users.id``. Уникален (см. модульный docstring).
        stripe_sub_id: ``sub_*`` от Stripe. Уникален.
        plan: ``Plan`` enum (FREE / PRO). Хранится как text — см.
            convention в ``shared_models.enums``.
        status: ``SubscriptionStatus`` enum (ACTIVE / PAST_DUE / CANCELED /
            INCOMPLETE). Это снимок последнего состояния, который мы
            получили от Stripe webhook'а. Для определения «должны ли мы
            давать доступ» используется ``services.entitlements.get_user_plan``,
            который смотрит и на status, и на ``current_period_end`` —
            см. ADR-0034 §«Failed payment policy».
        current_period_end: Timestamp окончания оплаченного периода.
            Stripe гарантирует, что платёж за следующий период будет
            попытаться сделан до этой даты. Используется для grace period.
        cancel_at_period_end: Если ``True``, Stripe автоматически
            переведёт подписку в CANCELED после ``current_period_end``.
            UI должен показать пользователю «подписка активна до X,
            после — Free».
    """

    __tablename__ = "stripe_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    stripe_sub_id: Mapped[str] = mapped_column(
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
        default=SubscriptionStatus.INCOMPLETE.value,
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


__all__ = ["StripeSubscription"]
