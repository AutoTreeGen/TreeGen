"""ExtractedFact — per-fact suggestion из AI source-extraction (Phase 10.2).

См. ADR-0059. Каждый ``SourceExtraction`` row порождает 0..N
``ExtractedFact`` rows — по одной на каждый persons/events/relationships
элемент в Claude-ответе. Ползватель ревьюит каждый fact независимо
(accept / reject / edit-and-accept).

Не наследует ``TreeEntityMixins``: служебная таблица, audit-trail
review-decisions. Soft-delete не нужен — rejected факты остаются для
metrics и preventing repeated suggestions.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class ExtractedFact(IdMixin, Base):
    """Один extract'нутый факт из source-extraction вызова.

    Attributes:
        extraction_id: FK на ``source_extractions.id`` (CASCADE).
        fact_index: Порядковый номер факта внутри одного extraction'а
            (0..N-1). Стабилен между accept/reject — позволяет UI
            отображать в порядке упоминания в исходнике.
        fact_kind: ``"person"`` / ``"event"`` / ``"relationship"``.
            Соответствует одной из Pydantic-моделей в
            ``ai_layer.types`` (``PersonExtract`` / ``EventExtract`` /
            ``RelationshipExtract``).
        data: jsonb-дамп соответствующей Pydantic-модели. UI парсит
            обратно через model_validate. Сохранение «как пришло от
            LLM» даёт reproducibility, а не «как мы интерпретировали».
        confidence: Self-assessed уверенность LLM, продублирована из
            ``data.confidence`` для индексации/сортировки.
        status: ``"pending"`` / ``"accepted"`` / ``"rejected"``.
            Lifecycle: pending → accepted | rejected. Идемпотент: повторный
            accept на already-accepted — no-op (caller'у решать).
        reviewed_at: Server-timestamp accept/reject. NULL пока pending.
        reviewed_by_user_id: Кто принял решение. NULL после user erasure.
        review_note: Optional пользовательский комментарий («заменил имя
            "Михаил" на "Михайло"», «факт уже есть в дереве»).
        created_at: Когда row создан (= вместе с ``SourceExtraction``).
    """

    __tablename__ = "extracted_facts"
    __table_args__ = (
        Index("ix_extracted_facts_extraction_index", "extraction_id", "fact_index"),
        Index("ix_extracted_facts_status", "status"),
        # Защита от мусорных value'ов: enum хранится как text, но
        # constraint в БД даёт быстрый fail-fast на bad insert'ах.
        CheckConstraint(
            "fact_kind IN ('person', 'event', 'relationship')",
            name="ck_extracted_facts_fact_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_extracted_facts_status",
        ),
    )

    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("source_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_index: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_note: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["ExtractedFact"]
