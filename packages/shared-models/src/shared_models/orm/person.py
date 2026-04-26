"""Person — персона в дереве."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import Sex
from shared_models.mixins import TreeEntityMixins

if TYPE_CHECKING:
    from shared_models.orm.name import Name


class Person(TreeEntityMixins, Base):
    """Персона.

    ``gedcom_xref`` — оригинальный ``@I123@`` из GED, нужен для round-trip.
    ``merged_into_person_id`` — для слияний (status=merged), указывает на «выживший» id.
    """

    __tablename__ = "persons"

    gedcom_xref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sex: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default=Sex.UNKNOWN.value,
        server_default=Sex.UNKNOWN.value,
    )
    merged_into_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )

    # relationships
    names: Mapped[list[Name]] = relationship(
        "Name",
        back_populates="person",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="Name.sort_order",
    )
