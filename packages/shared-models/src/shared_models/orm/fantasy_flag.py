"""FantasyFlag — advisory finding от fantasy filter (Phase 5.10 / ADR-0077).

Один scan дерева → много flag-rows. Каждая row — детект одного rule на
одном subject (person или relationship). UI показывает list с фильтром
``severity`` + ``dismissed=false``; user может dismiss как false-positive.

**Никогда не мутирует пользовательские GEDCOM-данные.** Flagging only —
эта таблица только хранит мнения rule-движка, оригинальные persons /
families / events не трогаются.

Service-table pattern: hard-CASCADE на tree (FK), SET NULL на user (FK
для ``dismissed_by``); без provenance/version_id/soft-delete (mirror
``hypothesis_compute_jobs``). Cleanup старых flag'ов — через retention
policy (TBD), не tombstone.

Брифовое предложение поместить в ``TREE_ENTITY_TABLES`` неверное:
TreeEntityMixins требуют provenance + version_id + soft-delete +
status + confidence_score (см. ADR-0003). Это аудит-запись о найденной
аномалии, не доменный факт дерева — поэтому ``SERVICE_TABLES``.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class FantasySeverity(enum.StrEnum):
    """Severity-уровни fantasy flag.

    Brief фиксирует 4 уровня (отличается от validator'а который имеет 3).

    - ``INFO``: соответствует «выглядит странно но скорее всего ОК».
    - ``WARNING``: подозрительно, человек должен взглянуть.
    - ``HIGH``: вероятно ошибка / fabrication.
    - ``CRITICAL``: логически невозможно (death before birth, циклы).
    """

    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class FantasyFlag(IdMixin, Base):
    """Один advisory flag о потенциальной fabrication / impossibility.

    Subject — либо persons (subject_person_id), либо relationship pair
    (subject_relationship_id). CHECK constraint в DB гарантирует, что
    хотя бы одно из них NOT NULL — flag без subject бесполезен для UI.

    ``confidence`` ∈ [0.0, 1.0]; ADR-0077 ограничивает максимум 0.95
    даже для critical-rules — оставляем место для legit edge-cases
    (very long-lived ancestors, immigrant date gaps).

    Dismiss-lifecycle:
        ``dismissed_at IS NULL``  — active flag, видим в UI.
        ``dismissed_at IS NOT NULL`` — user-acknowledged false positive.
        Undismiss = очистить три ``dismissed_*`` поля одной транзакцией.
    """

    __tablename__ = "fantasy_flags"
    __table_args__ = (
        # Главный list-query UI: «active high+ flags по дереву».
        Index(
            "ix_fantasy_flags_tree_severity_dismissed",
            "tree_id",
            "severity",
            "dismissed_at",
        ),
        # Person-detail-страница: «flags про эту персону».
        Index(
            "ix_fantasy_flags_subject_person",
            "subject_person_id",
            "dismissed_at",
        ),
        # Хотя бы один subject должен быть.
        CheckConstraint(
            "subject_person_id IS NOT NULL OR subject_relationship_id IS NOT NULL",
            name="ck_fantasy_flags_has_subject",
        ),
        CheckConstraint(
            "severity IN ('info', 'warning', 'high', 'critical')",
            name="ck_fantasy_flags_severity",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_fantasy_flags_confidence_range",
        ),
        # Dismiss-fields идут парой: либо все три, либо ни одного.
        # Если ``dismissed_at IS NOT NULL``, то и reason должен быть.
        CheckConstraint(
            "(dismissed_at IS NULL AND dismissed_by IS NULL AND dismissed_reason IS NULL)"
            " OR (dismissed_at IS NOT NULL)",
            name="ck_fantasy_flags_dismiss_consistency",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Один из двух subject'ов NOT NULL (CHECK выше).
    subject_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Relationship-flags (например, child-parent gap) — pointer на pair'у.
    # FK не делаем: relationship-id у нас не materialised. Используем
    # composite logic-id вида "FAM:F123" в evidence_json.
    subject_relationship_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    rule_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )

    dismissed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    dismissed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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


__all__ = ["FantasyFlag", "FantasySeverity"]
