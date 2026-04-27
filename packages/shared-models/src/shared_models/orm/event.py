"""Event — событие (рождение/смерть/брак/...) и EventParticipant."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin, TreeEntityMixins

if TYPE_CHECKING:
    from shared_models.orm.place import Place


class Event(TreeEntityMixins, Base):
    """Событие, происходящее с персоной/семьёй в конкретное время и месте.

    Для CUSTOM-событий заполняется ``custom_type``.
    Дата хранится трижды: ``date_raw`` (оригинал GEDCOM), ``date_start``/``date_end``
    (распарсенный диапазон), ``date_qualifier``/``date_calendar`` (метаданные).
    """

    __tablename__ = "events"
    __table_args__ = (
        CheckConstraint(
            "(event_type = 'CUSTOM' AND custom_type IS NOT NULL) OR event_type <> 'CUSTOM'",
            name="custom_type_required_for_custom",
        ),
    )

    event_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    custom_type: Mapped[str | None] = mapped_column(String, nullable=True)
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("places.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # GEDCOM date phrase бывает длинной ("FROM ABT 5 JAN 1850 (OS) TO BEF MAR 1855 ...")
    date_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    date_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True, index=True)
    date_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    date_qualifier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    date_calendar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    place: Mapped[Place | None] = relationship("Place", lazy="raise")


class EventParticipant(IdMixin, TimestampMixin, Base):
    """Участник события: персона ИЛИ семья (одно из двух не NULL)."""

    __tablename__ = "event_participants"
    __table_args__ = (
        CheckConstraint(
            "(person_id IS NOT NULL) OR (family_id IS NOT NULL)",
            name="participant_must_be_person_or_family",
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    family_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="principal",
        server_default="principal",
    )
