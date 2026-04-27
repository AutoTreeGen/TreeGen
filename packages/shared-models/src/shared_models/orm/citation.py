"""Citation — цитата из источника, привязанная к сущности (person/family/event)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, ProvenanceMixin, SoftDeleteMixin, TimestampMixin


class Citation(IdMixin, TimestampMixin, SoftDeleteMixin, ProvenanceMixin, Base):
    """Цитата.

    Полиморфная связь: ``entity_type`` ∈ {person, family, event} +
    ``entity_id``. Без полноценного FK (полиморфизм), целостность —
    на уровне приложения / триггера в проде.

    Phase 3.6: эта таблица — материализация ``SOURCE_CITATION`` из GEDCOM
    5.5.5 §3.5. К существующим ``page_or_section`` (PAGE), ``quoted_text``
    (TEXT), ``note`` (NOTE) и ``quality`` (производная из QUAY) добавлены:

    * ``quay_raw`` — сырой GEDCOM QUAY 0..3 (NULL — не задан в источнике).
      ``quality`` (float 0..1) хранит производное confidence по таблице
      0→0.1, 1→0.4, 2→0.7, 3→0.95, missing→0.5; ``quay_raw`` сохраняет
      исходное значение для round-trip и для будущей переоценки.
    * ``event_type`` — подтег EVEN: какое событие подтверждает цитата
      (например ``BIRT`` под ``DEAT`` означает, что свидетельство о
      смерти упоминает дату рождения). См. спеку §SOURCE_CITATION.
    * ``role`` — подтег EVEN > ROLE: роль персоны в этом событии
      (``WITN``, ``FATH`` и т. п.).
    """

    __tablename__ = "citations"
    __table_args__ = (
        # QUAY валиден в 0..3 (или NULL). Гард на уровне БД, чтобы импортёр
        # не записал, например, "5" из странного экспорта.
        CheckConstraint(
            "quay_raw IS NULL OR (quay_raw >= 0 AND quay_raw <= 3)",
            name="ck_citations_quay_raw_range",
        ),
        # Полиморфный поиск citations по entity (используется в trees API
        # для подтягивания citations к event'ам одним запросом).
        Index("ix_citations_entity", "entity_type", "entity_id"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    page_or_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quoted_text: Mapped[str | None] = mapped_column(String, nullable=True)
    quality: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
    )
    quay_raw: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)
