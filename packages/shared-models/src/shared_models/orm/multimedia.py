"""MultimediaObject — мультимедиа-артефакт + полиморфная связь с сущностями."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, Date, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin, TreeEntityMixins


class MultimediaObject(TreeEntityMixins, Base):
    """Файл (фото, документ, аудио, видео, PDF), привязанный к дереву.

    Сами байты хранятся в object storage (MinIO/GCS), здесь — метаданные и ссылка.
    """

    __tablename__ = "multimedia_objects"

    object_type: Mapped[str] = mapped_column(String(16), nullable=False, default="image")
    storage_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    caption: Mapped[str | None] = mapped_column(String, nullable=True)
    taken_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    object_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",  # имя колонки в БД, метод поля переименован, чтобы не конфликтовать с DeclarativeBase.metadata
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class EntityMultimedia(IdMixin, TimestampMixin, Base):
    """Полиморфная связь MultimediaObject → (entity_type, entity_id)."""

    __tablename__ = "entity_multimedia"

    multimedia_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("multimedia_objects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="primary",
        server_default="primary",
    )
