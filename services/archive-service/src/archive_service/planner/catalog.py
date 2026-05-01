"""Loader для archives_catalog.json — статического каталога архивов.

Catalog лежит рядом как package-data; в Phase 15.5b будет промотирован
в БД-таблицу. Загружается один раз при старте через
``Depends(get_catalog)`` (lru_cache внутри).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal

DigitizationLevel = Literal["none", "partial", "full"]

_CATALOG_PATH: Final[Path] = Path(__file__).parent / "archives_catalog.json"


@dataclass(frozen=True)
class CatalogArchive:
    """Один архив в каталоге.

    Все поля — обязательные; JSON-loader валидирует структуру при первом
    обращении и кеширует результат.
    """

    archive_id: str
    name: str
    location_country: str
    location_city: str
    time_range_start: int
    time_range_end: int
    languages: tuple[str, ...]
    digitization_level: DigitizationLevel


_VALID_LEVELS: Final[frozenset[str]] = frozenset({"none", "partial", "full"})


def _parse_entry(raw: dict[str, object]) -> CatalogArchive:
    level = raw["digitization_level"]
    if level not in _VALID_LEVELS:
        msg = f"Invalid digitization_level={level!r} for {raw.get('archive_id')!r}"
        raise ValueError(msg)
    languages_raw = raw["languages"]
    if not isinstance(languages_raw, list):
        msg = f"languages must be list for {raw.get('archive_id')!r}"
        raise TypeError(msg)
    ts_raw = raw["time_range_start"]
    te_raw = raw["time_range_end"]
    if not isinstance(ts_raw, (int, float)) or not isinstance(te_raw, (int, float)):
        msg = f"time_range_* must be numeric for {raw.get('archive_id')!r}"
        raise TypeError(msg)
    # mypy: ``level`` валидирован через _VALID_LEVELS, безопасный narrow к Literal.
    digitization: DigitizationLevel = level  # type: ignore[assignment]
    return CatalogArchive(
        archive_id=str(raw["archive_id"]),
        name=str(raw["name"]),
        location_country=str(raw["location_country"]).upper(),
        location_city=str(raw["location_city"]),
        time_range_start=int(ts_raw),
        time_range_end=int(te_raw),
        languages=tuple(str(lang).lower() for lang in languages_raw),
        digitization_level=digitization,
    )


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogArchive, ...]:
    """Прочитать JSON, распарсить, вернуть immutable tuple.

    Raises:
        FileNotFoundError: Если catalog отсутствует (баг сборки).
        ValueError: Если запись невалидна.
    """
    raw_text = _CATALOG_PATH.read_text(encoding="utf-8")
    raw_list = json.loads(raw_text)
    if not isinstance(raw_list, list):
        msg = "archives_catalog.json must contain a top-level array"
        raise TypeError(msg)
    return tuple(_parse_entry(entry) for entry in raw_list)


def get_catalog() -> tuple[CatalogArchive, ...]:
    """FastAPI Depends-совместимый getter (lru_cache делает идемпотентным)."""
    return load_catalog()
