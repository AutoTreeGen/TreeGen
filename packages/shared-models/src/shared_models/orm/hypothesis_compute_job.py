"""HypothesisComputeJob — bulk-compute job для hypothesis_runner (Phase 7.5).

См. brief Phase 7.5. Каждый ``POST /trees/{id}/hypotheses/compute-all``
порождает одну строку. Job хранит:

* ``status`` — lifecycle (см. ``HypothesisComputeJobStatus``).
* ``progress`` (jsonb) — `{processed, total, hypotheses_created}`,
  обновляется между batch'ами worker'ом.
* ``rule_ids`` (jsonb) — список rule_id'ов, которые компилирует runner
  (или null = use defaults). Сохранение даёт reproducibility: видно,
  какой набор правил исполнялся.
* ``cancel_requested`` — флаг, который worker проверяет между batch'ами.
  ``PATCH /cancel`` ставит его в true; worker граcefully завершает
  текущий batch и переходит в CANCELLED.
* ``started_at`` / ``finished_at`` — для idempotency timeout (≤1h
  ре-enqueue возвращает existing job).

Не наследует TreeEntityMixins — это служебная запись (audit-trail
job'ов), не доменная сущность дерева. Soft-delete не нужен; cleanup
старых job'ов — отдельная задача (Phase 7.5+ retention policy).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import HypothesisComputeJobStatus
from shared_models.mixins import IdMixin


class HypothesisComputeJob(IdMixin, Base):
    """Запись bulk-compute job'а."""

    __tablename__ = "hypothesis_compute_jobs"
    __table_args__ = (
        # Idempotency-friendly: быстрый lookup recent job на дерево.
        Index(
            "ix_hyp_jobs_tree_status_started",
            "tree_id",
            "status",
            "started_at",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=HypothesisComputeJobStatus.QUEUED.value,
        server_default=HypothesisComputeJobStatus.QUEUED.value,
        index=True,
    )

    # Каноничные параметры запуска. ``rule_ids=None`` (NULL в БД) →
    # worker использует _DEFAULT_RULE_CLASSES из bulk_hypothesis_runner.
    # Иначе — list[str] с whitelist'ом rule_id'ов.
    rule_ids: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Прогресс worker'а. Минимум: {processed: 0, total: 0,
    # hypotheses_created: 0}. Worker обновляет между batch'ами.
    progress: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {"processed": 0, "total": 0, "hypotheses_created": 0},
        server_default=text('\'{"processed": 0, "total": 0, "hypotheses_created": 0}\'::jsonb'),
    )

    # Cancel signal: ставится PATCH /cancel'ом, читается worker'ом.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Текстовая ошибка при FAILED. Ограничено разумным размером:
    # стек-трейсы — в logs/Sentry, тут только summary для UI.
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
