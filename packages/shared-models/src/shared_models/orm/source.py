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

    Phase 3.6: добавлены ``gedcom_xref`` (для дедупликации при повторных
    импортах одного и того же дерева), ``abbreviation`` (ABBR — короткое
    имя источника, часто единственный идентификатор у Geni-/FTM-экспортов)
    и ``text_excerpt`` (TEXT — извлечённый текст из источника, если был
    встроен в SOUR-запись).
    """

    __tablename__ = "sources"

    title: Mapped[str] = mapped_column(String, nullable=False)
    author: Mapped[str | None] = mapped_column(String, nullable=True)
    abbreviation: Mapped[str | None] = mapped_column(String, nullable=True)
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
    text_excerpt: Mapped[str | None] = mapped_column(String, nullable=True)
    # Оригинальный xref из GEDCOM (без обрамляющих @). Не уникален — один
    # и тот же файл могут импортнуть в несколько деревьев. Уникальность —
    # ``(tree_id, gedcom_xref)`` обеспечивается отдельным индексом в
    # миграции Phase 3.6.
    gedcom_xref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
