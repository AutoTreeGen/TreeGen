"""Внутренние DTO планировщика — между repo (DB) и scorer (pure).

Не экспортируется в API; для публичных моделей см. ``schemas.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class UndocumentedEvent:
    """Жизненное событие персоны без source citation.

    Поля выровнены с ``shared_models.orm.Event`` + denormalized
    ``Place`` атрибуты, чтобы scorer не дёргал ORM relationships.
    """

    event_id: uuid.UUID
    event_type: str  # BIRT / DEAT / MARR / ...
    date_start: dt.date | None
    date_end: dt.date | None
    place_country_iso: str | None  # ISO-3166 alpha-2
    place_city: str | None  # canonical_name или settlement
