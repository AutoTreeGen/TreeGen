"""DnaImport — операция импорта DNA-CSV (отдельно от GEDCOM-импортов).

Структура зеркалит ImportJob, но с DNA-специфичными полями:
- ``import_kind`` — что за CSV (match_list / shared_matches / segments).
- ``kit_id`` — для каких kit'а данные.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import DnaImportKind, DnaImportStatus, DnaPlatform
from shared_models.mixins import IdMixin


class DnaImport(IdMixin, Base):
    """Метаданные одного DNA-CSV импорта."""

    __tablename__ = "dna_imports"

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_kits.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_platform: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DnaPlatform.ANCESTRY.value,
    )
    import_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DnaImportKind.MATCH_LIST.value,
    )
    source_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=DnaImportStatus.QUEUED.value,
        server_default=DnaImportStatus.QUEUED.value,
        index=True,
    )
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
