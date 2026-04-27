"""Дедуп scoring для Person.

Алгоритм (ADR-0015) — composite weighted score:

* phonetic bucket match (DM ∪ Soundex) — weight 0.30.
* name Levenshtein (max(given, surname)) — weight 0.30.
* birth year proximity (±2 years tier) — weight 0.20.
* birth place fuzzy match — weight 0.20.

Hard filter: если оба пола известны (M/F) и не совпадают — score = 0.0.
Цель — не предлагать merge мужского и женского профилей даже при
сильном совпадении остального (типичный артефакт когда один и тот же
именованный родительский профиль ведут под разными гендерами).

Возвращаем `(composite, components)` чтобы UI Phase 4.5 мог объяснить
пользователю «совпали по DM-bucket + birth_year ±1».
"""

from __future__ import annotations

from dataclasses import dataclass

from entity_resolution.phonetic import daitch_mokotoff, soundex
from entity_resolution.places import place_match_score
from entity_resolution.string_matching import levenshtein_ratio, weighted_score

# Веса композитного scorer'а — единые для всех вызовов, лёгкая правка
# здесь же. См. ADR-0015 §«Алгоритмы / Persons».
_WEIGHTS: dict[str, float] = {
    "phonetic": 0.30,
    "name_levenshtein": 0.30,
    "birth_year": 0.20,
    "birth_place": 0.20,
}

# Год: точное совпадение, ±1, ±2 — три ступени уверенности.
_BIRTH_YEAR_EXACT = 1.0
_BIRTH_YEAR_CLOSE = 0.7
_BIRTH_YEAR_TOLERANCE = 2

# Половой фильтр: какие значения считаем «известными».
# CLAUDE.md / shared_models.enums.Sex: M, F, U (unknown), X (other/intersex).
_KNOWN_SEX = frozenset({"M", "F"})


@dataclass(frozen=True, slots=True)
class PersonForMatching:
    """Минимальный набор полей для scoring без БД-зависимостей.

    Все поля опциональные кроме ``surname``: без surname'а phonetic
    bucket теряет смысл, и persons всё равно попадают в один bucket
    «no surname» — что разумно (anonymous candidates просим user'а
    смотреть руками).
    """

    given: str | None
    surname: str | None
    birth_year: int | None
    death_year: int | None
    birth_place: str | None
    sex: str | None  # 'M' / 'F' / 'U' / 'X' / None


def _phonetic_match(a: str | None, b: str | None) -> float:
    """1.0 если совпали Soundex или пересекаются множества DM-кодов."""
    if not a or not b:
        return 0.0
    if soundex(a) == soundex(b) and soundex(a) != "":
        return 1.0
    a_dm = set(daitch_mokotoff(a))
    b_dm = set(daitch_mokotoff(b))
    if a_dm & b_dm:
        return 1.0
    return 0.0


def _name_levenshtein(a: PersonForMatching, b: PersonForMatching) -> float:
    """max ratio по given и surname (нормализованным)."""
    candidates: list[float] = []
    if a.given and b.given:
        candidates.append(levenshtein_ratio(a.given, b.given))
    if a.surname and b.surname:
        candidates.append(levenshtein_ratio(a.surname, b.surname))
    return max(candidates) if candidates else 0.0


def _birth_year_score(a: int | None, b: int | None) -> float | None:
    """Score для года рождения. None = «нет данных», вызывающий пропустит компонент.

    Returns:
        1.0 если совпало; 0.7 если |Δ| ≤ 2; 0.0 если дальше.
        ``None`` — если хоть у одного нет birth_year (вес перераспределяется).
    """
    if a is None or b is None:
        return None
    diff = abs(a - b)
    if diff == 0:
        return _BIRTH_YEAR_EXACT
    if diff <= _BIRTH_YEAR_TOLERANCE:
        return _BIRTH_YEAR_CLOSE
    return 0.0


def _birth_place_score(a: str | None, b: str | None) -> float | None:
    """Score для birth_place. None = нет данных у одной из сторон."""
    if not a or not b:
        return None
    return place_match_score(a, b)


def _sex_compatible(a: str | None, b: str | None) -> bool:
    """False только если оба известны (M/F) и различаются."""
    if a in _KNOWN_SEX and b in _KNOWN_SEX:
        return a == b
    return True


def person_match_score(
    a: PersonForMatching,
    b: PersonForMatching,
) -> tuple[float, dict[str, float]]:
    """Композитный person score + покомпонентный breakdown для UI.

    Returns:
        ``(composite, components)``. ``composite`` в [0, 1].
        ``components`` содержит индивидуальные scoring-сигналы
        (`phonetic`, `name_levenshtein`, `birth_year`, `birth_place`)
        с теми значениями, которые реально учли (отсутствующие
        компоненты не попадают в dict).

    Hard rule: если оба `sex` известны (M/F) и различаются — возвращаем
    ``(0.0, {})`` без дальнейших расчётов.
    """
    if not _sex_compatible(a.sex, b.sex):
        return 0.0, {}

    components: dict[str, float] = {
        "phonetic": _phonetic_match(a.surname, b.surname),
        "name_levenshtein": _name_levenshtein(a, b),
    }
    by = _birth_year_score(a.birth_year, b.birth_year)
    if by is not None:
        components["birth_year"] = by
    bp = _birth_place_score(a.birth_place, b.birth_place)
    if bp is not None:
        components["birth_place"] = bp

    composite = weighted_score(components, _WEIGHTS)
    return composite, components


__all__ = ["PersonForMatching", "person_match_score"]
