"""ReportBundleJob — bulk relationship-report job row (Phase 24.4 / ADR-0078).

POST к ``services/report-service`` со списком пар → одна row в этой
таблице (status=``queued``) → arq-task ``generate_report_bundle(job_id)``
переключает status ``queued → running → completed/failed/cancelled``,
инкрементируя ``completed_count`` / ``failed_count`` per pair.

**Service-table pattern** (mirror ``audio_sessions``, ``hypothesis_compute_jobs``,
``source_extractions``):

* Не наследует :class:`TreeEntityMixins` — это артефакт исследования /
  отгрузка контента, не genealogy-факт. Нет ``confidence_score`` /
  ``status`` (узкий :class:`BundleStatus` lifecycle), нет ``version_id``
  (worker мутирует ``completed_count`` атомарно — не ADR-0003 версионинг).
* Нет ``provenance`` JSONB — input spec уже хранится в
  ``relationship_pairs``; история — в ``error_summary``.
* Нет :class:`SoftDeleteMixin` — DELETE = hard delete после cancel/cleanup
  (или auto-purge по ``ttl_expires_at``). Иначе попадает под audit-listener,
  что для job-row избыточно.
* FK ``tree_id → trees.id ON DELETE CASCADE``: удаление дерева чистит
  bundle-jobs (GDPR-erasure ADR-0049).
* FK ``requested_by → users.id ON DELETE RESTRICT``: пользователь не может
  быть удалён, пока у него остаются bundle-jobs (audit-trail). Erasure
  pipeline сначала чистит jobs, потом user.

CHECK constraint на DB-уровне гарантирует
``total_count = jsonb_array_length(relationship_pairs)`` — invariant
выставляется приложением при INSERT и не должен дрейфовать.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class BundleStatus(enum.StrEnum):
    """Lifecycle ``report_bundle_jobs.status``.

    - ``QUEUED``: row создана API, ждёт worker'а.
    - ``RUNNING``: worker забрал, обрабатывает pairs.
    - ``COMPLETED``: bundle загружен в storage, ``storage_url`` установлен.
    - ``FAILED``: все pairs упали (или fatal job-level error). См. ``error_summary``.
    - ``CANCELLED``: caller дёрнул DELETE — worker остановлен, storage очищен.
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BundleOutputFormat(enum.StrEnum):
    """Формат финального bundle blob'а.

    - ``ZIP_OF_PDFS``: ZIP с per-pair PDFs + ``manifest.json``. Default.
    - ``CONSOLIDATED_PDF``: один PDF — cover + TOC + per-pair sections с
      непрерывной пагинацией. Для печати / forward'а одной кнопкой.
    """

    ZIP_OF_PDFS = "zip_of_pdfs"
    CONSOLIDATED_PDF = "consolidated_pdf"


class ReportBundleJob(IdMixin, TimestampMixin, Base):
    """Bulk relationship-report job (Phase 24.4).

    Поля:
        tree_id: FK ``trees.id ON DELETE CASCADE``.
        requested_by: FK ``users.id ON DELETE RESTRICT``. Кто инициировал.
        status: :class:`BundleStatus` text. Менять только worker'у.
        output_format: :class:`BundleOutputFormat` text.
        relationship_pairs: jsonb списка
            ``[{person_a_id, person_b_id, claimed_relationship?}]``.
            ``claimed_relationship`` опционален — NULL → auto-derive в worker.
        confidence_threshold: pass-through к 24.3 generator.
        total_count: derived от ``len(relationship_pairs)``; CHECK на DB-уровне.
        completed_count: атомарный счётчик успешных pair'ов.
        failed_count: атомарный счётчик упавших pair'ов.
        error_summary: jsonb списка ``[{pair_index, message}]`` для упавших.
        storage_url: nullable — путь в ObjectStorage после completion.
        started_at / completed_at: lifecycle markers (worker proставляет).
        ttl_expires_at: после этой точки bundle blob можно purge-нуть.
            Default = ``created_at + 7 days``; cleanup task сверяется.
    """

    __tablename__ = "report_bundle_jobs"
    __table_args__ = (
        # Anti-drift: total_count должен соответствовать длине
        # relationship_pairs JSONB-массива, выставленного INSERT'ом.
        # Гарантирует, что progress-метрики (completed_count / total_count)
        # имеют смысл.
        CheckConstraint(
            "total_count = jsonb_array_length(relationship_pairs)",
            name="ck_report_bundle_jobs_total_matches_pairs",
        ),
        CheckConstraint(
            "completed_count >= 0 AND failed_count >= 0",
            name="ck_report_bundle_jobs_counters_non_negative",
        ),
        CheckConstraint(
            "completed_count + failed_count <= total_count",
            name="ck_report_bundle_jobs_counters_within_total",
        ),
        # User-facing «my jobs in this tree» list: tree_id + status + recency.
        Index(
            "ix_report_bundle_jobs_tree_status_created",
            "tree_id",
            "status",
            "created_at",
        ),
        # TTL cleanup sweep: WHERE ttl_expires_at < now().
        Index("ix_report_bundle_jobs_ttl", "ttl_expires_at"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=BundleStatus.QUEUED.value,
    )
    output_format: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=BundleOutputFormat.ZIP_OF_PDFS.value,
    )
    relationship_pairs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    confidence_threshold: Mapped[float | None] = mapped_column(
        nullable=True,
    )
    total_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    error_summary: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    storage_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ttl_expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


__all__ = [
    "BundleOutputFormat",
    "BundleStatus",
    "ReportBundleJob",
]
