"""User — пользователь системы (привязан к external auth provider)."""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, SoftDeleteMixin, TimestampMixin


class User(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Пользователь.

    ``external_auth_id`` — sub из Clerk/Auth0 (см. ADR-0010 TBD), уникален.
    Личные данные минимальны: всё остальное приходит из auth-provider.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    external_auth_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
