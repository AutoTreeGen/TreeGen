"""DnaPileUpRegion (Phase 6.7a schema / ADR-0063).

Pile-up region — участок генома, который аномально часто появляется в
match-listах одной популяции. Классический пример: HLA-локус на хромосоме 6
у любых популяций; AJ-specific pile-ups на ряде хромосом из-за founder
effect'а.

Эти участки создают **много false-positive matches** в platform'ах
(Ancestry / MyHeritage), поэтому при clustering / triangulation их нужно
либо downweight'ить, либо явно скрывать.

Phase 6.7a только шипит таблицу. Detector
(:func:`dna_analysis.clustering.pile_up.segment_overlap_analysis`) — Phase 6.7b.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.types import new_uuid


class DnaPileUpRegion(Base):
    """Один pile-up регион по одной популяции.

    ``coverage_pct`` — % matches в популяции, делящих хотя бы часть этого
    региона. Numeric(5,2) → диапазон 0..999.99, реально 0..100.

    Уникальности на (chromosome, start, end, population) **не** ставим:
    разные алгоритмические runs могут породить близкие, но не
    идентичные интервалы; дедупликация — задача consumer-кода.
    """

    __tablename__ = "dna_pile_up_regions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    chromosome: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    end_position: Mapped[int] = mapped_column(BigInteger, nullable=False)
    population_label: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    coverage_pct: Mapped[float | None] = mapped_column(Numeric(precision=5, scale=2), nullable=True)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
