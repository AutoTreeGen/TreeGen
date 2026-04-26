"""DnaKit — ДНК-кит, привязанный к пользователю и опционально к персоне в дереве.

Один user может иметь несколько kits (свой + кита родственника). Kit — это
точка входа для match-list импорта.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import DnaPlatform, EthnicityPopulation
from shared_models.mixins import TreeEntityMixins


class DnaKit(TreeEntityMixins, Base):
    """ДНК-кит.

    ``external_kit_id`` — идентификатор кита на платформе (например, Ancestry test ID).
    ``person_id`` — связь с персоной в дереве (обычно сам user или его родственник).
    ``ethnicity_population`` — популяция для endogamy-коррекции (см. enum).
    """

    __tablename__ = "dna_kits"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_platform: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=DnaPlatform.ANCESTRY.value,
    )
    external_kit_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    test_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    ethnicity_population: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EthnicityPopulation.GENERAL.value,
        server_default=EthnicityPopulation.GENERAL.value,
    )
    consent_signed_at: Mapped[dt.datetime | None] = mapped_column(
        Date,
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
