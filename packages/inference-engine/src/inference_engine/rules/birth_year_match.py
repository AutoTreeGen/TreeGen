"""BirthYearMatchRule — первый concrete rule для Phase 7.0 demo.

Простой rule, доказывающий что plugin architecture работает: смотрит
на ``birth_year`` у subject_a и subject_b, выдаёт Evidence в зависимости
от близости.

Калибровка весов (Phase 7.0 — субъективные значения, валидация на
реальных данных — Phase 7.1+):

| Δ years | Direction   | Weight | Rationale |
|---------|-------------|--------|-----------|
| 0       | SUPPORTS    | 0.8    | Точное совпадение года рождения — сильный сигнал same-person, особенно в сочетании с другими rule's. Не максимальный (1.0), потому что близкие родственники одного поколения нередко тот же год. |
| 1–2     | SUPPORTS    | 0.4    | В рамках допустимой ошибки парсинга / приближённых дат (ABT, JUL/GREG переход). |
| 3–10    | (no evidence) | —    | Серая зона — не доказательство ни за, ни против. Возвращаем пустой list (NEUTRAL не нужен — нечего показывать в UI). |
| >10     | CONTRADICTS | 0.6    | Большая разница лет делает гипотезу same-person маловероятной. Не максимальный (1.0), потому что бывают опечатки в годах (1845 vs 1854). |

Если у одного из subjects нет ``birth_year`` — возвращаем пустой list
(rule неприменим, см. ADR-0016 §«InferenceRule Protocol»).

Этот rule — demo. Phase 7.1+ заменит / расширит его более thoroughly
калиброванным rule (включая prior'ы из tree-context — ADR-0016 §«Когда
пересмотреть»).
"""

from __future__ import annotations

from typing import Any

from inference_engine.types import Evidence, EvidenceDirection


class BirthYearMatchRule:
    """Сравнивает ``birth_year`` у subject_a / subject_b.

    Ожидаемый формат subject: ``dict`` с ключом ``"birth_year"`` (int) или его
    отсутствием. Другие ключи rule игнорирует.
    """

    rule_id: str = "birth_year_match"

    _EXACT_WEIGHT: float = 0.8
    _NEAR_WEIGHT: float = 0.4
    _DIVERGE_WEIGHT: float = 0.6
    _NEAR_THRESHOLD: int = 2
    _DIVERGE_THRESHOLD: int = 10

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del context

        a = subject_a.get("birth_year")
        b = subject_b.get("birth_year")
        if not isinstance(a, int) or not isinstance(b, int):
            return []

        diff = abs(a - b)

        if diff == 0:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=self._EXACT_WEIGHT,
                    observation=f"Birth year exact match ({a})",
                )
            ]
        if diff <= self._NEAR_THRESHOLD:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=self._NEAR_WEIGHT,
                    observation=f"Birth year within {self._NEAR_THRESHOLD} years (Δ={diff})",
                )
            ]
        if diff > self._DIVERGE_THRESHOLD:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.CONTRADICTS,
                    weight=self._DIVERGE_WEIGHT,
                    observation=f"Birth year diverges significantly (Δ={diff})",
                )
            ]
        # Серая зона 3–10 лет — нечего сказать ни за, ни против.
        return []
