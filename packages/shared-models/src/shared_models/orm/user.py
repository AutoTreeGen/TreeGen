"""User — пользователь системы (привязан к external auth provider)."""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, SoftDeleteMixin, TimestampMixin


class User(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Пользователь.

    ``external_auth_id`` — sub из Clerk/Auth0 (см. ADR-0010 TBD), уникален.
    Личные данные минимальны: всё остальное приходит из auth-provider.

    ``fs_token_encrypted`` (Phase 5.1, ADR-0027) — Fernet-зашифрованный
    JSON-payload с FamilySearch OAuth-токенами (access + refresh +
    expires_at). NULL = пользователь ещё не подключал FS-аккаунт.
    Расшифровка — :func:`parser_service.fs_oauth.tokens.decrypt_fs_token`.
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
    fs_token_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
