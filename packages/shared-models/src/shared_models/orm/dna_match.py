"""DnaMatch — одна строка из match-list какого-то kit'а.

В Ancestry/MyHeritage match — это другой человек, с которым ты делишь ДНК.
``shared_matches`` (m2m) — связи match-match через таблицу SharedMatch.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import TreeEntityMixins


class DnaMatch(TreeEntityMixins, Base):
    """Один match в match-list юзера.

    Имена полей выровнены с Ancestry CSV (см. dna-analysis/parsers).
    ``predicted_relationship`` — то что платформа сама прислала (e.g. "3rd cousin").
    Наша оценка родства — отдельно, в hypotheses (Phase 8).
    """

    __tablename__ = "dna_matches"

    kit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_kits.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_match_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # cM stats — основа для всех оценок родства.
    total_cm: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    largest_segment_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    segment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    predicted_relationship: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shared_match_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Привязка к персоне в дереве (если match идентифицирован).
    matched_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    notes: Mapped[str | None] = mapped_column(String, nullable=True)
