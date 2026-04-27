"""Строковые сравнения для дедупликации.

Тонкие обёртки над ``rapidfuzz``: нормализуем выход в [0, 1], скрываем
детали API за нашими именами, чтобы потребители (`sources.py`,
`places.py`, `persons.py`) не зависели напрямую от rapidfuzz и его
будущих breaking changes.
"""

from __future__ import annotations

from rapidfuzz import fuzz


def _normalize(s: str) -> str:
    """Lowercase + strip — предобработка перед любым ratio."""
    return s.strip().lower()


def levenshtein_ratio(a: str, b: str) -> float:
    """Levenshtein similarity в [0, 1]. 1.0 = идентичны.

    Рассчитывается на нормализованных (lower / strip) строках. Для двух
    пустых строк возвращает 1.0; для одной пустой — 0.0.
    """
    na = _normalize(a)
    nb = _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return float(fuzz.ratio(na, nb)) / 100.0


def token_set_ratio(a: str, b: str) -> float:
    """RapidFuzz token_set_ratio в [0, 1].

    Хорошо работает на «Slonim, Grodno, Russian Empire» vs «Slonim,
    Grodno» — порядок и подмножество токенов учитываются. Для пустых
    строк — те же правила что и у :func:`levenshtein_ratio`.
    """
    na = _normalize(a)
    nb = _normalize(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return float(fuzz.token_set_ratio(na, nb)) / 100.0


def weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Взвешенное среднее значений из ``scores`` с весами из ``weights``.

    Сумма весов не обязана быть 1 — нормализуем сами. Ключи, которых нет
    в ``scores``, пропускаются (не вкладывают в сумму и не уменьшают
    знаменатель). Это удобно, когда часть компонентов «недоступны»
    (например, ``birth_year`` отсутствует у одной из персон).

    Если ни одного известного компонента — возвращаем 0.0.
    """
    total_weight = 0.0
    weighted_sum = 0.0
    for key, weight in weights.items():
        if key not in scores:
            continue
        weighted_sum += scores[key] * weight
        total_weight += weight
    if total_weight <= 0.0:
        return 0.0
    return weighted_sum / total_weight


__all__ = ["levenshtein_ratio", "token_set_ratio", "weighted_score"]
