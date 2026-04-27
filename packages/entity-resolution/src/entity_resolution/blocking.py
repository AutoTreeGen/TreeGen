"""Blocking — группировка persons в bucket'ы для O(N × bucket_size).

Naive O(N²) сравнение растёт квадратично: 10k persons → 50M пар. На
средне-большом дереве (>5k персон) это уже минуты pure-Python работы.

ADR-0015 решение: блокирование по Daitch-Mokotoff (surname) кодам.
Каждая persona получает 1+ DM кодов; persons с общим кодом попадают
в общий bucket. Внутри bucket'а делаем full pairwise scoring.

Persons без surname'а складываем в специальный bucket ``""``. Это
отделяет anonymous кандидатов, чтобы не перемешивать их с named
persons (ложные совпадения по другим компонентам).
"""

from __future__ import annotations

from collections.abc import Iterable

from entity_resolution.persons import PersonForMatching
from entity_resolution.phonetic import daitch_mokotoff

_NO_SURNAME_BUCKET = ""


def block_by_dm(
    persons: Iterable[PersonForMatching],
) -> dict[str, list[PersonForMatching]]:
    """Сгруппировать persons по Daitch-Mokotoff (surname) кодам.

    Persona с surname'ом, дающим DM-коды [c1, c2], появляется в
    bucket'ах ``c1`` и ``c2`` (одна и та же ссылка). Persona без
    surname'а — в bucket'е ``""``.

    Внутри bucket'а можно делать наивный O(k²) попарный compare без
    риска перемолоть всё дерево.

    Args:
        persons: Любой iterable PersonForMatching. Будет полностью
            прочитан (один проход).

    Returns:
        ``dict[code -> list[Person]]``. Каждый bucket — отдельный
        list, без сортировки (порядок появления).
    """
    buckets: dict[str, list[PersonForMatching]] = {}
    for person in persons:
        if not person.surname:
            buckets.setdefault(_NO_SURNAME_BUCKET, []).append(person)
            continue
        codes = daitch_mokotoff(person.surname)
        if not codes:
            buckets.setdefault(_NO_SURNAME_BUCKET, []).append(person)
            continue
        for code in codes:
            buckets.setdefault(code, []).append(person)
    return buckets


__all__ = ["block_by_dm"]
