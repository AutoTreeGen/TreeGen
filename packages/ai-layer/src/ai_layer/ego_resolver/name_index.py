"""Name-index для ego_resolver: тонкий слой над 15.10
:class:`NameMatcher`. Берёт ``Mapping[UUID, PersonNames]`` и возвращает
ranked-list ``(person_id, score)`` для произвольного query'а.

Ничего не кэшируем сверх того что делает ``NameMatcher``: caller'ы
строят ``TreeContext`` per-request, и matcher per-call дёшев на типичном
дереве (<10k персон).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from entity_resolution.names import NameMatcher

from ai_layer.ego_resolver.types import PersonNames


@dataclass(frozen=True, slots=True)
class NameHit:
    """Один person-level name-hit.

    ``score`` — best score across всех name-форм этой персоны (given,
    surname, full_names, aliases). ``matched`` — какая именно форма
    дала best (для debug).
    """

    person_id: uuid.UUID
    score: float
    matched: str


def _candidate_strings(person: PersonNames) -> list[str]:
    """Все searchable name-форм одной персоны.

    Дедупим на уровне строки, чтобы NameMatcher не считал один и тот же
    surname дважды (и не насчитал boost от duplicate'ов).
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in (
        person.given,
        person.surname,
        *person.full_names,
        *person.aliases,
    ):
        if not raw:
            continue
        text = raw.strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    if person.given and person.surname:
        full = f"{person.given.strip()} {person.surname.strip()}"
        full_key = full.lower()
        if full_key not in seen:
            seen.add(full_key)
            out.append(full)
    return out


def search_names(
    persons: Mapping[uuid.UUID, PersonNames],
    query: str,
    *,
    candidates: set[uuid.UUID] | None = None,
    min_score: float = 0.7,
) -> list[NameHit]:
    """Ranks персон по name-match query'а.

    Args:
        persons: Все persons в дереве (от ``TreeContext.persons``).
        query: Free-text name. Любой script.
        candidates: Если задан — ограничиваем поиск этим под-set'ом
            (используется в mixed-mode «my wife Olga»: фильтруем уже
            сужённый walker'ом set жён). ``None`` — search across всех
            persons.
        min_score: Cutoff для NameMatcher (default 0.7 — стандарт 15.10).

    Returns:
        Sorted-by-score-DESC ``list[NameHit]``. Пустой — никто не прошёл.
    """
    if not query.strip():
        return []
    matcher = NameMatcher()
    target_ids = candidates if candidates is not None else set(persons.keys())
    results: list[NameHit] = []
    for pid in target_ids:
        person = persons.get(pid)
        if person is None:
            continue
        forms = _candidate_strings(person)
        if not forms:
            continue
        matches = matcher.match(query, forms, min_score=min_score)
        if not matches:
            continue
        # Best per-person (matches уже отсортированы в NameMatcher).
        best = matches[0]
        results.append(NameHit(person_id=pid, score=best.score, matched=best.candidate))
    results.sort(key=lambda h: (-h.score, str(h.person_id)))
    return results


__all__ = ["NameHit", "search_names"]
