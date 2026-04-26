"""Миксины для ORM-моделей AutoTreeGen.

Применяются ко всем доменным записям дерева (persons, families, events,
places, sources, notes, multimedia). См. ADR-0003.

- ``TimestampMixin``     — created_at / updated_at (server-side defaults).
- ``SoftDeleteMixin``    — deleted_at (None = запись активна).
- ``ProvenanceMixin``    — provenance jsonb для трекинга источников.
- ``VersionedMixin``     — version_id, инкрементируется на UPDATE через event listener.
- ``StatusMixin``        — status + confidence_score для оценки достоверности.
- ``TreeScopedMixin``    — tree_id FK на trees, разделяет данные по деревьям.

Композиция: ``class Person(TreeEntityMixins, Base):`` — собирает всё сразу.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column

from shared_models.enums import EntityStatus
from shared_models.types import new_uuid


@declarative_mixin
class IdMixin:
    """UUIDv7 PK с дефолтом на стороне приложения."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )


@declarative_mixin
class TimestampMixin:
    """created_at, updated_at — server-side defaults через ``now()``."""

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@declarative_mixin
class SoftDeleteMixin:
    """Soft delete: ``deleted_at`` + helper ``is_deleted``.

    Hard delete доступен только через сервисный GDPR-flow (см. ADR-0003).
    """

    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        """Помечена ли запись как удалённая (deleted_at установлен)."""
        return self.deleted_at is not None


@declarative_mixin
class ProvenanceMixin:
    """provenance jsonb: source_files, import_job_id, manual_edits.

    Структура свободная, но рекомендуемая:

    .. code-block:: json

       {
         "source_files": ["Ztree.ged"],
         "import_job_id": "01H...UUID",
         "manual_edits": [{"user_id": "...", "ts": "...", "fields": ["sex"]}]
       }
    """

    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )


@declarative_mixin
class VersionedMixin:
    """version_id, монотонно растёт на UPDATE.

    Используется для оптимистичных блокировок и отображения «текущей версии»
    в audit-log. Инкремент — через SQLAlchemy event listener в ``audit.py``.
    """

    version_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default=text("1"),
    )


@declarative_mixin
class StatusMixin:
    """status + confidence_score для оценки достоверности факта."""

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EntityStatus.PROBABLE.value,
        server_default=EntityStatus.PROBABLE.value,
    )
    confidence_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
    )


@declarative_mixin
class TreeScopedMixin:
    """tree_id FK на trees. Все доменные записи принадлежат конкретному дереву."""

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


class TreeEntityMixins(
    IdMixin,
    TreeScopedMixin,
    StatusMixin,
    ProvenanceMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """Композитный миксин для всех доменных записей дерева.

    Применяется к: persons, families, events, places, sources, citations,
    notes, multimedia_objects.
    """

    __abstract__ = True


class TreeOwnedMixins(
    IdMixin,
    ProvenanceMixin,
    VersionedMixin,
    TimestampMixin,
    SoftDeleteMixin,
):
    """Для записей, привязанных к дереву через owner (само ``trees``).

    У ``trees`` нет ``tree_id`` (оно само и есть дерево) и status (дерево —
    не факт, а контейнер).
    """

    __abstract__ = True
