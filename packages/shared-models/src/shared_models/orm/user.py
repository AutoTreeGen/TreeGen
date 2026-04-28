"""User — пользователь системы (привязан к external auth provider)."""

from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, SoftDeleteMixin, TimestampMixin


class User(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Пользователь.

    ``external_auth_id`` — generic legacy ``"local:{email}"`` /
    ``"clerk:{sub}"``-формат для backwards-compat (Phase 5.1 dev-flows).
    Уникален. Phase 4.10 (ADR-0033) добавил отдельный явный канал
    ``clerk_user_id`` для Clerk-аутентификации.

    ``clerk_user_id`` (Phase 4.10) — Clerk JWT ``sub`` (например,
    ``user_2abcDEF...``). Уникальный, nullable: legacy users из
    dev-flow без Clerk остаются с ``NULL``. JIT-create по этому
    полю — см. :mod:`parser_service.services.user_sync`.

    ``fs_token_encrypted`` (Phase 5.1, ADR-0027) — Fernet-зашифрованный
    JSON-payload с FamilySearch OAuth-токенами (access + refresh +
    expires_at). NULL = пользователь ещё не подключал FS-аккаунт.
    Расшифровка — :func:`parser_service.fs_oauth.tokens.decrypt_fs_token`.

    ``email_opt_out`` (Phase 12.2, ADR-0039) — пользователь отключил
    transactional-email. Email-service ставит ``status=skipped_optout``
    и не вызывает провайдера. Phase 12.x добавит per-kind opt-out
    (как notification_preferences); пока — глобальный флаг.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True, index=True)
    external_auth_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    clerk_user_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        unique=True,
        index=True,
        default=None,
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    # Phase 4.10b (ADR-0038): IANA timezone string, например ``"Europe/Moscow"``.
    # Nullable — большинство юзеров оставит дефолт; backend'у нужен
    # для рендера дат в email/notification'ах в local time.
    timezone: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    fs_token_encrypted: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
    )
    email_opt_out: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
