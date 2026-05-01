"""Integration test: Zhitnitzky duplicates → composite ≥ 0.75 (Phase 7.5).

Этот тест — главный demo Phase 7.1: показывает как dedup-suggestions
из entity-resolution превращаются в полную evidence-chain через
inference-engine. Реальный фамильный case (владелец репо).

Включает все 4 rule'а Phase 7.1:
- SurnameMatchRule (Daitch-Mokotoff с Cyrillic transliteration)
- BirthYearMatchRule (exact match → SUPPORTS 0.4)
- BirthPlaceMatchRule (place_match_score с Cyrillic)
- SexConsistencyRule (consistent → silent)

Phase 7.5 (ADR-0065): Bayesian fusion заменила weighted sum.
Ожидаемый composite: 1 − (1−0.5)(1−0.4)(1−0.32) ≈ 0.796 — ниже
старого «clamp до 1.0», но качественно гипотеза по-прежнему
сильная. Threshold снижен с 0.85 до 0.75 чтобы отражать новую
семантику (Bayesian насыщается медленнее на счётном множестве
SUPPORTS).

Mixed Cyrillic/Latin данные специально, чтобы проверить что rule'ы
действительно матчат через транслитерацию.
"""

from __future__ import annotations

from inference_engine import (
    EvidenceDirection,
    HypothesisType,
    compose_hypothesis,
    register_rule,
)
from inference_engine.rules.birth_year import BirthYearMatchRule
from inference_engine.rules.place import BirthPlaceMatchRule
from inference_engine.rules.sex import SexConsistencyRule
from inference_engine.rules.surname import SurnameMatchRule


def _register_default_rules() -> None:
    """Зарегистрировать все 4 Phase 7.1 rule'а (autouse fixture очистит после)."""
    register_rule(SurnameMatchRule())
    register_rule(BirthYearMatchRule())
    register_rule(BirthPlaceMatchRule())
    register_rule(SexConsistencyRule())


def test_zhitnitzky_duplicates_get_high_score() -> None:
    """Demo: один и тот же человек, два варианта записи → composite ≥ 0.85.

    Vlad Zhitnitzky (Latin) vs Vladimir Жytницкий (mixed Cyrillic/Latin).
    Ожидаем 4 rules:
    - surname_dm_match: SUPPORTS (DM-bucket overlap после транслитерации)
    - birth_year_match: SUPPORTS exact (1945)
    - birth_place_match: SUPPORTS (Dnepropetrovsk vs Днепропетровск с транслитом)
    - sex_consistency: silent (оба M, не CONTRADICTS)
    """
    _register_default_rules()

    a = {
        "given": "Vladimir",
        "surname": "Zhitnitzky",
        "birth_year": 1945,
        "birth_place": "Dnepropetrovsk",
        "sex": "M",
    }
    b = {
        "given": "Volodya",
        "surname": "Житницкий",
        "birth_year": 1945,
        "birth_place": "Днепропетровск",
        "sex": "M",
    }

    h = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a=a,
        subject_b=b,
        context={"hypothesis_type": "same_person"},
    )

    assert h.composite_score >= 0.75, (
        f"composite_score {h.composite_score} below 0.75 (Phase 7.5 threshold); "
        f"evidences={[(ev.rule_id, ev.direction.value, ev.weight) for ev in h.evidences]}"
    )

    rule_ids = {ev.rule_id for ev in h.evidences}
    assert "surname_dm_match" in rule_ids
    assert "birth_year_match" in rule_ids
    assert "birth_place_match" in rule_ids
    # sex_consistency silent — на consistency её не должно быть в списке.
    assert "sex_consistency" not in rule_ids

    # Все evidences должны быть SUPPORTS — для этого case никаких contradicts.
    for ev in h.evidences:
        assert ev.direction is EvidenceDirection.SUPPORTS


def test_sex_mismatch_kills_zhitnitzky_match() -> None:
    """Counter-case: тот же DM/year/place pattern, но разный пол → composite < threshold.

    Phase 7.5 (ADR-0065): SexConsistencyRule выпускает CONTRADICTS,
    что в новой aggregation отнимает фиксированные 0.1, не свой weight.
    Bayesian fusion от трёх SUPPORTS (~0.79) минус 0.1 ≈ 0.69 —
    всё ещё ниже 0.75 threshold для confident SAME_PERSON.
    Trade-off задокументирован в ADR-0065 §«Consequences».
    """
    _register_default_rules()

    a = {"surname": "Zhitnitzky", "birth_year": 1945, "birth_place": "Dnepr", "sex": "M"}
    b = {"surname": "Zhitnitzky", "birth_year": 1945, "birth_place": "Dnepr", "sex": "F"}

    h = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a=a,
        subject_b=b,
        context={"hypothesis_type": "same_person"},
    )

    rule_ids = {ev.rule_id for ev in h.evidences}
    assert "sex_consistency" in rule_ids, "sex contradiction must be present"
    contradiction = next(ev for ev in h.evidences if ev.rule_id == "sex_consistency")
    assert contradiction.direction is EvidenceDirection.CONTRADICTS
    # Phase 7.5: Bayesian fusion + флэт-штраф 0.1 → composite ниже
    # SAME_PERSON threshold 0.75. Точное значение зависит от
    # place_match_score, но строго ниже 0.75.
    assert h.composite_score < 0.75


def test_completely_unrelated_persons_low_score() -> None:
    """Counter-case: разные фамилии / даты / места → composite близок к 0."""
    _register_default_rules()

    a = {"surname": "Smith", "birth_year": 1850, "birth_place": "Boston", "sex": "M"}
    b = {"surname": "Zhitnitzky", "birth_year": 1945, "birth_place": "Dnepr", "sex": "M"}

    h = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a=a,
        subject_b=b,
        context={"hypothesis_type": "same_person"},
    )

    # Surname не пересекается — нет supports.
    # Birth year |Δ|=95 → CONTRADICTS для same_person.
    # Birth place: «Boston» vs «Dnepr» → CONTRADICTS.
    # Phase 7.5: 0 SUPPORTS → fusion=0, − penalty(0.2) → 0, floor=0.05.
    # Test threshold 0.10 покрывает все варианты.
    # Composite: 0 - 0.6 = max(0, ...) = 0.
    assert h.composite_score <= 0.10
