"""Source — источник (книга, метрика, перепись, сайт, интервью, ДНК-тест)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import SourceType
from shared_models.mixins import TreeEntityMixins


class Source(TreeEntityMixins, Base):
    """Источник информации.

    Конкретные ссылки источников на сущности — через ``citations``.
    """

    __tablename__ = "sources"

    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    publication: Mapped[str | None] = mapped_column(String, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SourceType.OTHER.value,
        server_default=SourceType.OTHER.value,
    )
    repository: Mapped[str | None] = mapped_column(String, nullable=True)
    repository_id: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    publication_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
