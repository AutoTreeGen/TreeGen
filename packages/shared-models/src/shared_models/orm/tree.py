"""Tree — корневой контейнер генеалогических данных пользователя."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import CollaboratorRole, TreeVisibility
from shared_models.mixins import IdMixin, TreeOwnedMixins

if TYPE_CHECKING:
    from shared_models.orm.user import User


class Tree(TreeOwnedMixins, Base):
    """Дерево.

    Все доменные записи (persons, families, events, ...) принадлежат конкретному
    дереву. Дерево само не имеет ``tree_id`` (само и есть scope).
    """

    __tablename__ = "trees"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TreeVisibility.PRIVATE.value,
        server_default=TreeVisibility.PRIVATE.value,
    )
    default_locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    # Voice-to-tree privacy gate (Phase 10.9a / ADR-0064 §B1).
    # Нужно отдельно от ``settings`` jsonb, потому что privacy-инвариант
    # должен быть инспектируем schema-tooling'ом и query-able по индексу
    # для админ-аудитов («у скольких деревьев consent дан»).
    audio_consent_egress_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # String, *не* enum: Phase 10.9.x добавит ``self-hosted-whisper`` —
    # не хочется миграции ради нового допустимого значения. Pydantic
    # на app-слое применяет ``Literal[...]``.
    audio_consent_egress_provider: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    # relationships
    owner: Mapped[User] = relationship(
        "User",
        foreign_keys=[owner_user_id],
        lazy="raise",
    )


class TreeCollaborator(IdMixin, Base):
    """Соавтор дерева (read/edit-доступ помимо владельца)."""

    __tablename__ = "tree_collaborators"

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CollaboratorRole.VIEWER.value,
    )
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
