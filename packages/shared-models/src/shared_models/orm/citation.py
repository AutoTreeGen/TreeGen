"""Citation — цитата из источника, привязанная к сущности (person/family/event)."""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, ProvenanceMixin, SoftDeleteMixin, TimestampMixin


class Citation(IdMixin, TimestampMixin, SoftDeleteMixin, ProvenanceMixin, Base):
    """Цитата.

    Полиморфная связь: ``entity_type`` ∈ {person, family, event} +
    ``entity_id``. Без полноценного FK (полиморфизм), целостность —
    на уровне приложения / триггера в проде.
    """

    __tablename__ = "citations"

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    page_or_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quoted_text: Mapped[str | None] = mapped_column(String, nullable=True)
    quality: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
    )
    note: Mapped[str | None] = mapped_column(String, nullable=True)
