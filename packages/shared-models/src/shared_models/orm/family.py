"""Family — семья (брак/партнёрство), FamilyChild — связь ребёнок–семья."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import RelationType
from shared_models.mixins import IdMixin, TimestampMixin, TreeEntityMixins


class Family(TreeEntityMixins, Base):
    """Семья.

    ``husband_id`` / ``wife_id`` — два «принципала». Для однополых пар роли
    значатся условно (исторически в GEDCOM это husband/wife). Дети — через
    ``family_children``.
    """

    __tablename__ = "families"

    gedcom_xref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    husband_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    wife_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class FamilyChild(IdMixin, TimestampMixin, Base):
    """Связь «семья → ребёнок» с типом отношения и порядком рождения."""

    __tablename__ = "family_children"

    family_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("families.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    child_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=RelationType.BIOLOGICAL.value,
    )
    birth_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
