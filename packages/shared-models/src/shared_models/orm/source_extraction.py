"""SourceExtraction — run-level лог одного AI-extraction вызова (Phase 10.2).

См. ADR-0059. Хранит per-run cost (tokens), prompt-/model-версию для
reproducibility, raw_response jsonb для debug/analytics. Per-fact результаты
живут в отдельной таблице ``extracted_facts``.

Не наследует ``TreeEntityMixins``: это служебный audit-trail AI-вызовов,
не доменная сущность дерева. Soft-delete не нужен (immutable history,
purge через retention-политику в Phase 10.5+).

``tree_id`` денормализуем (FK дублирует ``Source.tree_id``) — нужен для
GDPR-erasure (CASCADE с tree) и для permission-gate'ов.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    DateTime,
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


class SourceExtraction(IdMixin, Base):
    """Запись одного AI-extraction вызова на одном Source.

    Attributes:
        source_id: FK на ``sources.id`` (CASCADE — extraction живёт
            пока живёт source; soft-delete'нутый source оставляет
            extraction для audit).
        tree_id: Денормализованный FK на ``trees.id`` (для GDPR-erasure
            CASCADE и для лookup'а user→tree без JOIN на sources).
        requested_by_user_id: Кто инициировал extraction. NULL после
            user erasure (SET NULL, ADR-0049).
        model_version: Имя Claude-модели, например ``claude-sonnet-4-6``.
            Сохраняем actual-model из response, не requested (Anthropic
            может откатить на fallback в редких случаях).
        prompt_version: Имя prompt-template без ``.md`` —
            ``"source_extractor_v1"``. Roll-forward / rollback виден в
            данных без миграций.
        status: ``ai_layer.AIRunStatus`` value (``pending`` /
            ``completed`` / ``failed``).
        input_tokens: tokens prompt'а.
        output_tokens: tokens ответа.
        raw_response: jsonb со shape из ``ai_layer.runs.build_raw_response``:
            ``{model, prompt_version, stop_reason, input_tokens,
            output_tokens, parsed}``. Пустой ``{}`` если status=PENDING/
            FAILED-до-вызова.
        error: Текст ошибки при ``status=failed``. NULL иначе.
        created_at: Когда row создан (= когда вызов стартовал).
        completed_at: Когда status стал ``completed`` или ``failed``.
            NULL пока ``pending``.
    """

    __tablename__ = "source_extractions"
    __table_args__ = (
        Index("ix_source_extractions_user_created", "requested_by_user_id", "created_at"),
        Index("ix_source_extractions_source_created", "source_id", "created_at"),
        Index("ix_source_extractions_tree_status", "tree_id", "status"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    raw_response: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


__all__ = ["SourceExtraction"]
