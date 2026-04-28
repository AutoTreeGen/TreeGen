"""ImportJob — операция импорта (GEDCOM, DNA CSV, archive match)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import ImportJobStatus, ImportSourceKind
from shared_models.mixins import IdMixin


class ImportJob(IdMixin, Base):
    """Метаданные одного импорта.

    Идемпотентность по ``source_sha256`` + ``tree_id`` (UNIQUE-индекс).
    Повторный импорт того же файла не дублирует данные — entity resolution в Phase 7.

    Phase 3.5 — async-импорт через arq:

    * ``progress`` (jsonb) — снапшот текущего шага worker'а (stage, current,
      total, message, ts). Обновляется ProgressPublisher между стадиями;
      также публикуется в Redis pubsub-канал ``job-events:{job_id}`` для
      live-стрима через SSE.
    * ``cancel_requested`` — флаг graceful cancel'а. Worker читает между
      стадиями и переводит status → cancelled.
    """

    __tablename__ = "import_jobs"
    __table_args__ = ()  # UNIQUE на (tree_id, source_sha256) добавится в миграции

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
    source_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ImportSourceKind.GEDCOM.value,
    )
    source_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ImportJobStatus.QUEUED.value,
        server_default=ImportJobStatus.QUEUED.value,
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

    # Снапшот прогресса (Phase 3.5). NULL — worker ещё не публиковал.
    # Структура совпадает с ``ImportJobProgress`` Pydantic-схемой.
    progress: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        default=None,
    )

    # Cancel-сигнал (Phase 3.5). Ставится PATCH /imports/{id}/cancel,
    # читается worker'ом между стадиями.
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
