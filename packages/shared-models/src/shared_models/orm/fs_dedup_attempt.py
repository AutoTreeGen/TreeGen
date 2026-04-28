"""FsDedupAttempt — кандидаты на дедупликацию из FS-import (Phase 5.2.1).

См. ``docs/research/phase-5-2-dedup-discovery.md`` (Option C). Узкая
таблица для FS-flagged пар. Для каждой пары *(только что
импортированная FS-персона, локальная не-FS персона того же дерева)* со
score ≥ threshold importer вставляет одну строку в active-состоянии
(``rejected_at IS NULL AND merged_at IS NULL``); review-UI ставит либо
``rejected_at`` (отказ), либо ``merged_at`` (после merge через Phase 4.6).

Идемпотентность:

* **Active-pair guard**: партиал-уникальный индекс
  ``(tree_id, fs_person_id, candidate_person_id) WHERE rejected_at IS
  NULL AND merged_at IS NULL`` — нельзя одновременно держать две
  активные attempt-записи на одну направленную пару.
* **Direction matters**: пара ``(fs_person_id, candidate_person_id)`` —
  направленная, без lex-reorder. ``(A=fs, B=local)`` и ``(B=fs,
  A=local)`` — разные attempts (разные семантики «что мы импортируем»).
* **fs_pid idempotency**: при повторном импорте того же FS-person
  caller проверяет, нет ли уже attempt'а с ``merged_at`` для этого
  ``fs_pid`` — если есть, кандидат уже был ассимилирован, повторно не
  предлагаем. Индекс ``(tree_id, fs_pid)`` это ускоряет.
* **Cooldown**: для отвергнутых ``rejected_at`` пар importer 90 дней не
  предлагает ту же пару повторно. Партиал-уникальный индекс этого не
  enforce'ит (по дизайну — старая reject'нутая запись + новый attempt
  не конфликтуют), фильтр в caller.

CLAUDE.md §5: только suggestion. Никакого автомата merge. Терминальное
действие — Phase 4.6 manual-merge endpoint, который на success
проставляет ``merged_at``.
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
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class FsDedupAttempt(IdMixin, TimestampMixin, Base):
    """Запись об одной FS-flagged dedup-попытке (см. ADR-pending Phase 5.2.1).

    Поля:
        tree_id: Дерево (multi-tenant scope).
        fs_person_id: Persons.id той записи, что была только что
            импортирована из FamilySearch (направленная сторона A).
        candidate_person_id: Persons.id локального не-FS кандидата
            (сторона B). Direction matters — не пере-сортируем lex.
        score: Composite confidence от
            ``entity_resolution.person_match_score``, диапазон [0, 1].
        reason: Свободный label источника attempt'а — для FS-import
            ``"fs_import_match"``; зарезервировано для будущих rule'ов.
        fs_pid: FamilySearch external person id (например, ``"KW7S-VQJ"``).
            Берётся из ``Person.provenance['fs_person_id']`` (см. ADR-0017).
            Используется для идемпотентности при повторном импорте.
        rejected_at: Timestamp отказа user'а (review UI). NULL если
            attempt всё ещё active или уже merged.
        merged_at: Timestamp успешного merge через Phase 4.6 endpoint.
            NULL если attempt не merged.
        provenance: Произвольный jsonb для дополнительного контекста
            (например, ``import_job_id``, components-breakdown скорера).
    """

    __tablename__ = "fs_dedup_attempts"
    __table_args__ = (
        # Active-pair guard: одна active attempt на направленную пару
        # (tree_id, fs_person_id, candidate_person_id). Reject'нутые
        # и merged-записи не блокируют — они уже не active.
        Index(
            "ux_fs_dedup_attempts_active_pair",
            "tree_id",
            "fs_person_id",
            "candidate_person_id",
            unique=True,
            postgresql_where=text("rejected_at IS NULL AND merged_at IS NULL"),
        ),
        # Idempotency lookup для повторного FS-import'а того же fs_pid:
        # ``WHERE tree_id = :t AND fs_pid = :p AND merged_at IS NOT NULL``.
        Index(
            "ix_fs_dedup_attempts_tree_id_fs_pid",
            "tree_id",
            "fs_pid",
        ),
        # score в [0, 1] — guard для будущих изменений scorer'а.
        CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_fs_dedup_attempts_score_range",
        ),
        # fs_person_id != candidate_person_id — direction matters, но
        # пара одной и той же персоны самой с собой бессмысленна.
        CheckConstraint(
            "fs_person_id <> candidate_person_id",
            name="ck_fs_dedup_attempts_distinct_persons",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fs_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fs_pid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejected_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    merged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
