"""Place — место (с поддержкой исторических границ и алиасов)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.mixins import IdMixin, SoftDeleteMixin, TimestampMixin, TreeEntityMixins


class Place(TreeEntityMixins, Base):
    """Место.

    ``canonical_name`` — наиболее официальное современное название.
    Исторические/языковые варианты — в ``place_aliases`` (Wilno/Vilna/Vilnius/Вильно).
    """

    __tablename__ = "places"

    # Free-form: в реальных дампах admin1/admin2 могут быть длинными
    # ("Минская губерния, Российская Империя" и т.п.) — лимиты не нужны.
    canonical_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    country_code_iso: Mapped[str | None] = mapped_column(String(8), nullable=True)
    admin1: Mapped[str | None] = mapped_column(String, nullable=True)
    admin2: Mapped[str | None] = mapped_column(String, nullable=True)
    settlement: Mapped[str | None] = mapped_column(String, nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    historical_period_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    historical_period_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    aliases: Mapped[list[PlaceAlias]] = relationship(
        "PlaceAlias",
        back_populates="place",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class PlaceAlias(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Альтернативное название места (язык, период, транслитерация)."""

    __tablename__ = "place_aliases"

    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    script: Mapped[str | None] = mapped_column(String(32), nullable=True)
    romanized: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    valid_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)

    place: Mapped[Place] = relationship("Place", back_populates="aliases")
