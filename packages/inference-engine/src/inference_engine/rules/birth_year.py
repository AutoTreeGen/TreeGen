"""BirthYearMatchRule — proximity дат рождения.

Phase 7.0 docstring обещал этот rule в Phase 7.0, но он не был
зашиплен в первой итерации scaffold'а. Phase 7.1 добавляет его рядом с
SurnameMatchRule / PlaceMatchRule, чтобы интеграционный тест
(«Zhitnitzky duplicates») мог собрать composite ≥ 0.85.

Тiers (mirrored from ``entity_resolution.persons._birth_year_score``):

* exact match → SUPPORTS, weight 0.4. Сильный сигнал, особенно для
  same_person hypothesis.
* |Δ| ∈ {1, 2} → SUPPORTS, weight 0.25. Менее уверенный (handles
  типичные ошибки переписи / GEDCOM-конверсии).
* |Δ| ≥ 10 → CONTRADICTS, weight 0.30. Только для same_person —
  для parent_child наоборот ожидаем разрыв в 15–40 лет.
* Иначе (3 ≤ Δ < 10) — no evidence: серая зона.

Subject-контракт: ключ ``"birth_year"`` (int) — opt'нo. Отсутствие у
любой стороны → пустой list.
"""

from __future__ import annotations

from typing import Any

from inference_engine.types import Evidence, EvidenceDirection

_EXACT_WEIGHT = 0.4
_CLOSE_WEIGHT = 0.25
_FAR_CONTRADICTS_WEIGHT = 0.30

_CLOSE_TOLERANCE = 2
_FAR_THRESHOLD = 10


class BirthYearMatchRule:
    """Birth year proximity → SUPPORTS / CONTRADICTS / no evidence."""

    rule_id = "birth_year_match"

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        a_year = subject_a.get("birth_year")
        b_year = subject_b.get("birth_year")
        if a_year is None or b_year is None:
            return []

        try:
            diff = abs(int(a_year) - int(b_year))
        except (TypeError, ValueError):
            return []

        if diff == 0:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=_EXACT_WEIGHT,
                    observation=f"Birth year exact match ({a_year})",
                )
            ]
        if diff <= _CLOSE_TOLERANCE:
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.SUPPORTS,
                    weight=_CLOSE_WEIGHT,
                    observation=f"Birth years close: {a_year} vs {b_year} (Δ={diff})",
                )
            ]

        # FAR contradicts применяется только к same_person hypothesis.
        # Для parent_child / siblings разрыв в годы — нормально.
        if diff >= _FAR_THRESHOLD and context.get("hypothesis_type") == "same_person":
            return [
                Evidence(
                    rule_id=self.rule_id,
                    direction=EvidenceDirection.CONTRADICTS,
                    weight=_FAR_CONTRADICTS_WEIGHT,
                    observation=(
                        f"Birth years diverge for same_person: {a_year} vs {b_year} (Δ={diff})"
                    ),
                )
            ]
        return []


__all__ = ["BirthYearMatchRule"]
