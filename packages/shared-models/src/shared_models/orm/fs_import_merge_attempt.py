"""FsImportMergeAttempt — audit-лог решений merge-mode FS-импорта (Phase 5.2).

Отдельная таблица от ``fs_dedup_attempts`` (Phase 5.2.1). Phase 5.2.1
писала **suggestion**'ы для review-queue после того как все FS-persons
уже были вставлены в дерево; Phase 5.2 пишет **decision**'ы, принятые
*до* INSERT'а: для каждой FS-персоны merger выбирает SKIP / MERGE /
CREATE_AS_NEW, и сюда падает одна row с финальной стратегией, score'ом
и pointer'ом на matched local Person'а (если был).

Эта таблица — иммутабельный лог; UI/тесты используют её как
audit-trail. Мы намеренно не делаем «active vs rejected»-state как в
``FsDedupAttempt`` (там state-машина для review-flow); здесь решение
уже принято и реверсу не подлежит — только новый импорт может
произвести новый attempt.

Идемпотентность: каждый new attempt — это запись в журнале import'а;
повторный import тех же FS persons породит новый набор attempts с
другим ``import_job_id``. Корреляция «было ли уже такое решение
раньше» — через ``(tree_id, fs_pid)`` query.

См. ADR-0017 §«Merge-mode decision tree» (Phase 5.2 extension).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class FsImportMergeAttempt(IdMixin, TimestampMixin, Base):
    """Запись об одном decision'е merge-mode FS-импорта (Phase 5.2).

    Поля:
        tree_id: Целевое дерево (multi-tenant scope).
        import_job_id: ID ``ImportJob``-а, в рамках которого было принято
            это решение. На каждый attempt — одна row, на каждый job —
            от 0 до N rows (зависит от размера pedigree).
        fs_pid: FamilySearch external person id (``KW7S-VQJ``). Это
            «вход» — то, что мы пришли импортировать.
        strategy: ``MergeStrategy`` value: ``skip`` / ``merge`` /
            ``create_as_new``.
        matched_person_id: Persons.id, на который приземлилось решение:

            * для ``skip`` — существующий Person с тем же ``fs_pid`` (если
              find-by-pid сработал), иначе ``None``;
            * для ``merge`` — high-confidence local match;
            * для ``create_as_new`` — top-кандидат из mid-confidence
              коридора (если был), иначе ``None``.
        score: Composite score [0, 1] от
            ``entity_resolution.person_match_score``. ``None`` если
            кандидатов вообще не было (чистый CREATE_AS_NEW), либо если
            decision принят по ``fs_pid`` идемпотентности (SKIP без
            scorer'а).
        score_components: Покомпонентный breakdown скорера (jsonb).
            Используется UI/audit для explainability «почему MERGE».
            Пустой dict если ``score is None``.
        needs_review: True для CREATE_AS_NEW в mid-confidence коридоре
            (0.5 ≤ score < 0.9). UI Phase 4.5 показывает такие attempt'ы
            как «возможные дубликаты, проверьте». False для high-confidence
            decision'ов (SKIP/MERGE) и для low-confidence CREATE_AS_NEW
            (где близких кандидатов нет).
        reason: Свободный label источника решения, например
            ``"fs_pid_idempotent"``, ``"high_confidence_match"``,
            ``"mid_confidence_review"``, ``"no_candidates"``.
        provenance: Дополнительный jsonb-контекст (например, snapshot
            scorer'а, версия rules, debug-инфо).
    """

    __tablename__ = "fs_import_merge_attempts"
    __table_args__ = (
        # Lookup «какие решения принял этот job?» — для метрик/аудита.
        Index(
            "ix_fs_import_merge_attempts_job_id",
            "import_job_id",
        ),
        # Lookup «какие решения принимались по этому fs_pid в дереве?» —
        # для cross-import audit'а.
        Index(
            "ix_fs_import_merge_attempts_tree_id_fs_pid",
            "tree_id",
            "fs_pid",
        ),
        # score либо в [0, 1], либо NULL.
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="ck_fs_import_merge_attempts_score_range",
        ),
        # strategy ограничен значениями MergeStrategy. Хранится как text
        # (см. enums.py § «как text, не postgresql ENUM»).
        CheckConstraint(
            "strategy IN ('skip', 'merge', 'create_as_new')",
            name="ck_fs_import_merge_attempts_strategy",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    import_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("import_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    fs_pid: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_components: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    needs_review: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
