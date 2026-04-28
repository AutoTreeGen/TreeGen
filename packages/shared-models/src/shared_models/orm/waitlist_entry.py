"""WaitlistEntry — лид с лендинга (Phase 4.12).

Простая модель: email + locale + timestamp. Без user_id, без provenance,
без soft-delete (это не tree-domain entity, а маркетинговый touch).
ADR-0035 §«Lead capture» фиксирует scope: достаточно собрать email
+ метку «откуда» (Accept-Language → locale), для рассылки feature
updates. Не EU-резидент-aware GDPR (Phase 11 биллинг подключит
proper consent + analytics-pixel).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class WaitlistEntry(IdMixin, Base):
    """Подписка на маркетинговый waitlist."""

    __tablename__ = "waitlist_entries"
    __table_args__ = (UniqueConstraint("email", name="uq_waitlist_entries_email"),)

    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
