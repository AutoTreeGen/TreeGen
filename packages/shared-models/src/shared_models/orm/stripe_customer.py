"""StripeCustomer — связь user ↔ Stripe Customer (Phase 12.0, ADR-0034).

Один user → один stripe_customer_id. Если user отменил подписку и
позже вернулся, мы переиспользуем тот же Stripe Customer (Stripe API
позволяет создавать новые subscriptions на старого Customer'а — это
корректно с точки зрения биллинга и помогает не плодить дубли).

Личные данные (карта, billing address, имя) хранятся **только** на
стороне Stripe (см. ADR-0034 §«GDPR»). У нас в БД — только маппинг ID.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class StripeCustomer(IdMixin, TimestampMixin, Base):
    """Маппинг внутреннего user_id → Stripe Customer ID.

    Атрибуты:
        id: UUIDv7 (наш internal PK).
        user_id: FK на ``users.id`` (UUID — User использует ``IdMixin``).
        stripe_customer_id: ``cus_*`` от Stripe. Уникален: один user не
            должен иметь несколько Customer'ов (даже после cancel'а
            подписки переиспользуем существующего).
    """

    __tablename__ = "stripe_customers"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    stripe_customer_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )


__all__ = ["StripeCustomer"]
