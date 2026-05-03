"""Маппинг raw relationship-строки от платформы в каноничный enum (Phase 16.3).

Каждая платформа форматирует прогноз родства по-своему: «3rd Cousin»,
«1st—2nd Cousin», «Distant Cousin», «Brother», «Possible Sibling».
Эта функция — единая точка нормализации; парсеры используют её
после извлечения raw-строки.

Anti-drift (ADR-0072): мы ничего сами не предсказываем — функция
лишь bucket'ирует чужое предсказание в наш enum для cross-platform
aggregation. Raw-строка всё равно сохраняется в
``MatchListEntry.predicted_relationship_raw`` и в ``raw_payload``.
"""

from __future__ import annotations

import re

from shared_models.enums import PredictedRelationship

# Каноничные substring-паттерны → bucket. Порядок проверки важен:
# более специфичные сначала (parent_child перед any «child» mention).
_PATTERN_TO_BUCKET: tuple[tuple[re.Pattern[str], PredictedRelationship], ...] = (
    (
        re.compile(r"\bparent\b|\bchild\b|\bmother\b|\bfather\b|\bson\b|\bdaughter\b", re.I),
        PredictedRelationship.PARENT_CHILD,
    ),
    (re.compile(r"\bidentical\s+twin\b", re.I), PredictedRelationship.PARENT_CHILD),
    # Half-* / uncle-aunt / grand-* — проверяется *перед* full-sibling, потому что
    # «half-sister» содержит «sister» substring и попало бы в FULL_SIBLING иначе.
    (
        re.compile(
            r"\bhalf[-\s]?(?:brother|sister|sibling)\b|"
            r"\b(?:uncle|aunt|niece|nephew)\b|"
            r"\bgrand(?:parent|mother|father|child|son|daughter)\b",
            re.I,
        ),
        PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT,
    ),
    (
        re.compile(
            r"\b(?:full\s+)?(?:brother|sister|sibling)\b",
            re.I,
        ),
        PredictedRelationship.FULL_SIBLING,
    ),
    (
        re.compile(r"\b1st[-\s]+cousin\b|\bfirst\s+cousin\b", re.I),
        PredictedRelationship.FIRST_COUSIN,
    ),
    (
        re.compile(r"\b2nd[-\s]+cousin\b|\bsecond\s+cousin\b", re.I),
        PredictedRelationship.SECOND_COUSIN,
    ),
    (
        re.compile(r"\b3rd[-\s]+cousin\b|\bthird\s+cousin\b", re.I),
        PredictedRelationship.THIRD_COUSIN,
    ),
    (
        re.compile(
            r"\b4th[-\s]+cousin\b|\bfourth\s+cousin\b|"
            r"\b5th[-\s]+cousin\b|\bfifth\s+cousin\b|"
            r"\b6th[-\s]+cousin\b|\bsixth\s+cousin\b",
            re.I,
        ),
        PredictedRelationship.FOURTH_TO_SIXTH_COUSIN,
    ),
    (re.compile(r"\bdistant\b|\b7th\b|\b8th\b|\bremote\b", re.I), PredictedRelationship.DISTANT),
)


def normalise_relationship(raw: str | None) -> PredictedRelationship:
    """Пеервести raw-relationship-string в :class:`PredictedRelationship`.

    Возвращает ``UNKNOWN``, если raw — None, пустая строка, или ни один
    canonical-паттерн не совпал. Не падает на новых форматах: нам
    достаточно одного safe-fallback'а; raw-строка всегда сохраняется
    отдельно для последующей переоценки.

    Args:
        raw: Строка от платформы (e.g. «3rd Cousin», «Distant Cousin»).

    Returns:
        PredictedRelationship bucket. ``UNKNOWN`` — если не классифицируется.
    """
    if raw is None:
        return PredictedRelationship.UNKNOWN
    text = raw.strip()
    if not text:
        return PredictedRelationship.UNKNOWN
    for pattern, bucket in _PATTERN_TO_BUCKET:
        if pattern.search(text):
            return bucket
    return PredictedRelationship.UNKNOWN
