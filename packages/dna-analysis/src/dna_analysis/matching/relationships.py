"""Relationship prediction по total shared cM (см. ADR-0014).

Источник статистики — **Shared cM Project 4.0** by Blaine Bettinger,
March 2020 (DNA Painter calculator). Лицензия — CC-BY 4.0; attribution
обязательно сохраняется в docstring класса и в JSON output матчер'а
(`source` field в Phase 6.1 Task 5 CLI).

Ссылки:
    - https://dnapainter.com/tools/sharedcmv4
    - https://thegeneticgenealogist.com/  (Bettinger blog)

Probability в RelationshipRange — это **нормализованная плотность**
вероятности по cM-диапазонам, не posterior probability. Для строгой
Bayes-оценки нужен prior (генеалогический контекст дерева) — это
Phase 6.4. См. ADR-0014 §«Source relationship table».

Privacy: эта функция не работает с raw genotypes/positions/rsids —
только с агрегатами cM. Logs тут не нужны.
"""

from __future__ import annotations

import math
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# Hardcoded таблица из Shared cM Project 4.0 (Bettinger, CC-BY 4.0).
# Каждая запись: (label, cm_min, cm_max, mean_cm). Min/max — практический
# 95% CI наблюдаемых значений в большой выборке известных родств.
# Группировка отражает биологическую неотличимость по cM (например,
# grandparent / aunt-uncle / half-sibling неразличимы только по total cM
# без phasing — потребует Phase 6.4).
_TABLE: Final[tuple[tuple[str, int, int, int], ...]] = (
    ("Identical twin / Same person", 3400, 4000, 3487),
    ("Parent / Child", 2376, 3720, 3485),
    ("Full sibling", 1613, 3488, 2613),
    (
        "Grandparent / Grandchild / Aunt / Uncle / Niece / Nephew / Half-sibling",
        1156,
        2311,
        1759,
    ),
    (
        "1st cousin / Great-grandparent / Great-aunt / Great-uncle / Half-aunt / Half-uncle",
        396,
        1397,
        866,
    ),
    (
        "1st cousin once removed / Great-great-grandparent / Half 1st cousin",
        102,
        979,
        432,
    ),
    ("2nd cousin / 1st cousin twice removed", 41, 592, 229),
    ("2nd cousin once removed / Half 2nd cousin", 14, 353, 122),
    ("3rd cousin", 0, 234, 73),
    ("3rd cousin once removed", 0, 192, 48),
    ("4th cousin", 0, 139, 35),
    ("5th cousin or more distant", 0, 117, 21),
)

_UNRELATED_LABEL: Final = "Unrelated / noise"
_NOISE_THRESHOLD_CM: Final = 7.0  # синхронизировано с ADR-0014 default min_cm

_SOURCE_ATTRIBUTION: Final = "Shared cM Project 4.0 (Bettinger, CC-BY 4.0)"


class RelationshipRange(BaseModel):
    """Один кандидат relationship для данного total shared cM.

    Attributes:
        label: Человекочитаемое имя (или сгруппированное «1st cousin /
            Great-grandparent / ...» если cM-неразличимы).
        probability: Нормализованная density в cM-диапазоне; сумма по
            всем кандидатам в результате = 1.0. НЕ posterior — для
            истинного posterior нужен prior из дерева (Phase 6.4).
        cm_range: (min, max) в cM по Shared cM Project 4.0.
        source: Атрибуция reference data — всегда Shared cM Project 4.0.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str = Field(..., min_length=1)
    probability: float = Field(..., ge=0.0, le=1.0)
    cm_range: tuple[int, int]
    source: str = _SOURCE_ATTRIBUTION


def predict_relationship(
    total_shared_cm: float,
    longest_segment_cm: float = 0.0,  # noqa: ARG001 — Phase 6.4 (phasing/IBD2)
) -> list[RelationshipRange]:
    """Возвращает ranked список плауsibly relationships для total cM.

    Алгоритм:
        - Если total ниже noise-floor (< 7 cM) → один кандидат
          «Unrelated / noise» с probability 1.0.
        - Иначе ищем все relationship-метки, в чьём диапазоне `total_shared_cm`
          лежит. Для каждого считаем score через Gaussian-like density
          вокруг mean ((total - mean) / sigma) с sigma = (max - min) / 4
          (≈ 95% CI).
        - Нормализуем scores → probabilities, sort desc.

    longest_segment_cm зарезервирован для Phase 6.4 (phasing / IBD2 для
    разрешения parent vs sibling и т.п.). Сейчас не используется.

    Args:
        total_shared_cm: Сумма cM всех segments (≥ 0).
        longest_segment_cm: Длина самого длинного сегмента; reserved.

    Returns:
        Sorted-by-probability список RelationshipRange. Минимум один
        элемент: при отсутствии candidates возвращаем «Unrelated / noise».
    """
    if total_shared_cm < 0:
        msg = "total_shared_cm must be non-negative"
        raise ValueError(msg)

    if total_shared_cm < _NOISE_THRESHOLD_CM:
        return [
            RelationshipRange(
                label=_UNRELATED_LABEL,
                probability=1.0,
                cm_range=(0, 0),
            )
        ]

    candidates: list[tuple[str, int, int, float]] = []
    for label, cm_min, cm_max, mean in _TABLE:
        if cm_min <= total_shared_cm <= cm_max:
            sigma = max((cm_max - cm_min) / 4.0, 1.0)
            # Гауссова density: (1/σ) * exp(-z²/2). Деление на σ —
            # narrower распределения (identical twin, full sibling) дают
            # более острый пик и выигрывают у broader (parent/child) когда
            # total попадает в их центр. См. ADR-0014 §«Source relationship
            # table» — density, не posterior.
            score = math.exp(-0.5 * ((total_shared_cm - mean) / sigma) ** 2) / sigma
            candidates.append((label, cm_min, cm_max, score))

    if not candidates:
        # Total за пределами всех known диапазонов — выше identical twin
        # (огромное число) или, обработав уже < 7 cM выше, нет matches.
        # Для total > max(identical_twin_max) — самая близкая метка по mean.
        if total_shared_cm > _TABLE[0][2]:
            return [
                RelationshipRange(
                    label=_TABLE[0][0],
                    probability=1.0,
                    cm_range=(_TABLE[0][1], _TABLE[0][2]),
                )
            ]
        return [
            RelationshipRange(
                label=_UNRELATED_LABEL,
                probability=1.0,
                cm_range=(0, 0),
            )
        ]

    total_score = sum(score for _, _, _, score in candidates)
    candidates.sort(key=lambda c: c[3], reverse=True)
    return [
        RelationshipRange(
            label=label,
            probability=score / total_score,
            cm_range=(cm_min, cm_max),
        )
        for label, cm_min, cm_max, score in candidates
    ]
