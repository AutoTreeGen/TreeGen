"""SharedMatch — пара matches которые делят ДНК между собой.

Антисимметричная пара: храним один раз с условием ``match_a_id < match_b_id``
(лексикографически по UUID), чтобы не дублировать.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class SharedMatch(IdMixin, TimestampMixin, Base):
    """Связь "match A делит ДНК с match B" в той же match-list.

    Используется AutoCluster алгоритмом (Leeds Method + Louvain) для группировки
    matches по ветвям семьи.
    """

    __tablename__ = "shared_matches"
    __table_args__ = (
        UniqueConstraint("kit_id", "match_a_id", "match_b_id", name="uq_shared_matches_triple"),
        CheckConstraint("match_a_id <> match_b_id", name="ck_shared_matches_not_self"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_kits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_matches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    match_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_matches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Опциональное cM-значение между A и B (Ancestry exports не содержат, MyHeritage иногда содержит).
    shared_cm: Mapped[float | None] = mapped_column(Float, nullable=True)

    source_platform: Mapped[str | None] = mapped_column(String(32), nullable=True)
