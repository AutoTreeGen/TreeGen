"""Дедуп scoring для Source.

Алгоритм (см. ADR-0015):

* token_set_ratio на title  — weight 0.7. Покрывает «Lubelskie parish
  records 1838» vs «Lubelskie Parish 1838» (порядок, casing, лишние
  токены).
* Jaccard на authors split — weight 0.2. Authors часто пишут списком
  через запятую / точку с запятой, нам нужно «есть ли пересечение».
* Boost +0.1 если abbreviation совпадают exactly (после lower / strip).
  Abbreviation — короткий код типа «LubParish1838» — самый сильный
  индикатор duplicate'а.

Threshold по умолчанию (в `services/dedup_finder.py`): 0.85+ — likely
duplicate.
"""

from __future__ import annotations

from entity_resolution.string_matching import token_set_ratio

_AUTHOR_SEPARATORS = (",", ";")


def _split_authors(value: str | None) -> set[str]:
    """Развалить строку authors в set нормализованных имён.

    Поддерживаются разделители ``,`` и ``;``. Пустые токены
    отбрасываются. Имена нормализуются (lower / strip).
    """
    if not value:
        return set()
    raw = value
    for sep in _AUTHOR_SEPARATORS:
        raw = raw.replace(sep, "|")
    return {part.strip().lower() for part in raw.split("|") if part.strip()}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity в [0, 1]."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def source_match_score(
    a_title: str,
    a_author: str | None,
    a_abbrev: str | None,
    b_title: str,
    b_author: str | None,
    b_abbrev: str | None,
) -> float:
    """Композитный score сходства двух Source-записей в [0, 1].

    Args:
        a_title, b_title: Названия источников. Сравниваем token_set
            (порядок и casing не важны).
        a_author, b_author: Авторы (часто список через запятую).
            Если оба ``None`` — компонент игнорируется (вес перераспределяется).
        a_abbrev, b_abbrev: Аббревиатуры. Если совпадают exactly —
            +0.1 boost (capped at 1.0).

    Returns:
        Score в [0, 1]. ≥ 0.85 → likely duplicate.
    """
    title_score = token_set_ratio(a_title, b_title)

    a_authors = _split_authors(a_author)
    b_authors = _split_authors(b_author)
    has_authors = bool(a_authors and b_authors)
    author_score = _jaccard(a_authors, b_authors) if has_authors else 0.0

    # Веса: если authors отсутствует у одной из сторон — отдаём весь
    # вес title'у (0.9), не штрафуя за нехватку данных.
    title_weight = 0.7 if has_authors else 0.9
    author_weight = 0.2 if has_authors else 0.0

    base = title_score * title_weight + author_score * author_weight

    abbrev_match = (
        a_abbrev is not None
        and b_abbrev is not None
        and a_abbrev.strip().lower() == b_abbrev.strip().lower()
        and a_abbrev.strip() != ""
    )
    if abbrev_match:
        base += 0.1

    return min(base, 1.0)


__all__ = ["source_match_score"]
