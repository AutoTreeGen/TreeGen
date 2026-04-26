"""Name — имя персоны (одна персона = много имён в разных языках/контекстах)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import NameType
from shared_models.mixins import IdMixin, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from shared_models.orm.person import Person


class Name(IdMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Имя персоны в одной из форм/языков.

    Одна персона может иметь много имён: birth-name, married-name, hebrew-name,
    nickname, AKA, romanized variants. Каждое — отдельная строка.
    """

    __tablename__ = "names"

    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Free-form имена: реальные GEDCOM-файлы содержат всё что угодно — длинные
    # AKA-наборы, иврит-имена с диакритикой, конкатенированные suffix'ы.
    # Снимаем VARCHAR-лимиты, валидация — на Pydantic-уровне (см. schemas/entities.py).
    given_name: Mapped[str | None] = mapped_column(String, nullable=True)
    surname: Mapped[str | None] = mapped_column(String, nullable=True)
    prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    suffix: Mapped[str | None] = mapped_column(String, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String, nullable=True)
    patronymic: Mapped[str | None] = mapped_column(String, nullable=True)
    maiden_surname: Mapped[str | None] = mapped_column(String, nullable=True)
    name_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NameType.BIRTH.value,
    )
    script: Mapped[str | None] = mapped_column(String(32), nullable=True)
    romanized: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    person: Mapped[Person] = relationship("Person", back_populates="names")
