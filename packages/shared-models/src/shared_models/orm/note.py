"""Note — текстовая заметка + полиморфная связь с сущностями."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin, TreeEntityMixins


class Note(TreeEntityMixins, Base):
    """Заметка/комментарий, может быть привязана к нескольким сущностям через ``entity_notes``."""

    __tablename__ = "notes"

    body: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="text/plain",
        server_default="text/plain",
    )
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)


class EntityNote(IdMixin, TimestampMixin, Base):
    """Полиморфная связь Note → (entity_type, entity_id)."""

    __tablename__ = "entity_notes"

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
