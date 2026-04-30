"""Tree — корневой контейнер генеалогических данных пользователя."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false, func, text
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

    # Phase 15.4 (ADR-0062) — Protected Tree Mode + change-proposal policy.
    # ``protected=True`` → все мутации обязаны идти через
    # ``tree_change_proposals`` (review-flow, см. Phase 15.4b/c). Default
    # ``False`` сохраняет существующее поведение (direct edits) для всех
    # уже существующих деревьев — solo-users не получают friction.
    protected: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    # Свободно-форменный jsonb для policy-настроек:
    # ``{require_evidence_for: ["parent_child","spouse"], min_reviewers: 1,
    #   allow_owner_bypass: false}``. Pydantic-схема валидации — в
    # ``api_gateway.schemas.ProtectionPolicy``. Default ``{}`` — пустая
    # policy (только бинарный флаг ``protected`` имеет силу, ничего не
    # требуется кроме самого PR-flow'а).
    protection_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
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
