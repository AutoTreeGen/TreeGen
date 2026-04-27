"""Тесты BirthYearMatchRule + integration test.

Этот demo-rule доказывает что plugin protocol работает: его apply()
вызывается composer'ом, evidence попадает в Hypothesis, score
композируется по weighted-sum формуле.
"""

from __future__ import annotations

from inference_engine import (
    EvidenceDirection,
    HypothesisType,
    InferenceRule,
    compose_hypothesis,
    register_rule,
)
from inference_engine.rules import BirthYearMatchRule


class TestBirthYearMatchRule:
    def test_satisfies_protocol(self) -> None:
        assert isinstance(BirthYearMatchRule(), InferenceRule)

    def test_rule_id_is_stable(self) -> None:
        """rule_id — часть provenance contract'а; менять его — breaking change."""
        assert BirthYearMatchRule.rule_id == "birth_year_match"

    def test_exact_match_supports_with_high_weight(self) -> None:
        rule = BirthYearMatchRule()
        evidences = rule.apply({"birth_year": 1945}, {"birth_year": 1945}, {})
        assert len(evidences) == 1
        ev = evidences[0]
        assert ev.direction is EvidenceDirection.SUPPORTS
        assert ev.weight == 0.8
        assert "1945" in ev.observation
        assert ev.rule_id == "birth_year_match"

    def test_near_match_supports_with_lower_weight(self) -> None:
        rule = BirthYearMatchRule()
        evidences = rule.apply({"birth_year": 1845}, {"birth_year": 1846}, {})
        assert len(evidences) == 1
        assert evidences[0].direction is EvidenceDirection.SUPPORTS
        assert evidences[0].weight == 0.4

    def test_two_years_difference_still_supports(self) -> None:
        rule = BirthYearMatchRule()
        evidences = rule.apply({"birth_year": 1850}, {"birth_year": 1852}, {})
        assert len(evidences) == 1
        assert evidences[0].direction is EvidenceDirection.SUPPORTS

    def test_grey_zone_returns_empty(self) -> None:
        """Δ от 3 до 10 — нечего сказать ни за, ни против."""
        rule = BirthYearMatchRule()
        for diff in (3, 5, 7, 10):
            evidences = rule.apply({"birth_year": 1900}, {"birth_year": 1900 + diff}, {})
            assert evidences == [], f"Δ={diff} должен дать empty"

    def test_diverging_years_contradicts(self) -> None:
        rule = BirthYearMatchRule()
        evidences = rule.apply({"birth_year": 1820}, {"birth_year": 1880}, {})
        assert len(evidences) == 1
        assert evidences[0].direction is EvidenceDirection.CONTRADICTS
        assert evidences[0].weight == 0.6
        assert "60" in evidences[0].observation

    def test_missing_birth_year_returns_empty(self) -> None:
        rule = BirthYearMatchRule()
        assert rule.apply({}, {"birth_year": 1900}, {}) == []
        assert rule.apply({"birth_year": 1900}, {}, {}) == []
        assert rule.apply({}, {}, {}) == []

    def test_non_int_birth_year_returns_empty(self) -> None:
        """Защита от мусора во входных dict'ах: только int — валидный birth_year."""
        rule = BirthYearMatchRule()
        assert rule.apply({"birth_year": "1900"}, {"birth_year": 1900}, {}) == []
        assert rule.apply({"birth_year": None}, {"birth_year": 1900}, {}) == []
        assert rule.apply({"birth_year": 1900.5}, {"birth_year": 1900}, {}) == []

    def test_extra_subject_keys_are_ignored(self) -> None:
        """Rule смотрит только на birth_year — не должен ломаться от лишних ключей."""
        rule = BirthYearMatchRule()
        a = {"given": "Vladimir", "surname": "Zhitnitzky", "birth_year": 1945, "x": 1}
        b = {"given": "Volodya", "surname": "Житницкий", "birth_year": 1945}
        evidences = rule.apply(a, b, {})
        assert len(evidences) == 1
        assert evidences[0].direction is EvidenceDirection.SUPPORTS

    def test_symmetry(self) -> None:
        """abs(a-b) == abs(b-a) — порядок subjects не должен влиять на результат."""
        rule = BirthYearMatchRule()
        forward = rule.apply({"birth_year": 1900}, {"birth_year": 1950}, {})
        reverse = rule.apply({"birth_year": 1950}, {"birth_year": 1900}, {})
        assert len(forward) == len(reverse) == 1
        assert forward[0].direction is reverse[0].direction
        assert forward[0].weight == reverse[0].weight


class TestIntegration:
    def test_compose_same_person_hypothesis_zhitnitzky(self) -> None:
        """Demo из brief'а: Vladimir 1945 vs Volodya 1945 → composite ≥ 0.5.

        Этот тест — единственный «end-to-end» в Phase 7.0: registering rule,
        composer применяет, hypothesis получает supporting evidence,
        composite_score проходит порог. Когда Phase 7.1 добавит больше rule's
        (surname Daitch-Mokotoff, given-name diminutive), composite уйдёт
        к 0.9+ как в примере из CLAUDE.md.
        """
        register_rule(BirthYearMatchRule())
        a = {"given": "Vladimir", "surname": "Zhitnitzky", "birth_year": 1945}
        b = {"given": "Volodya", "surname": "Житницкий", "birth_year": 1945}
        hypothesis = compose_hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a=a,
            subject_b=b,
            context={},
        )
        assert hypothesis.composite_score >= 0.5
        assert hypothesis.hypothesis_type is HypothesisType.SAME_PERSON
        assert any(ev.rule_id == "birth_year_match" for ev in hypothesis.evidences)
        # Provenance: каждый Evidence знает свой источник.
        for ev in hypothesis.evidences:
            assert ev.rule_id  # non-empty

    def test_compose_with_diverging_birth_years_low_score(self) -> None:
        """Counter-evidence first-class: расходящиеся годы → низкий score."""
        register_rule(BirthYearMatchRule())
        a = {"birth_year": 1800}
        b = {"birth_year": 1900}
        hypothesis = compose_hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a=a,
            subject_b=b,
        )
        assert hypothesis.composite_score == 0.0
        # Но Evidence о расхождении остаётся видимым в hypothesis для UI.
        contradicts = [
            ev for ev in hypothesis.evidences if ev.direction is EvidenceDirection.CONTRADICTS
        ]
        assert len(contradicts) == 1

    def test_compose_with_no_birth_year_data_yields_no_evidence(self) -> None:
        """Если у subjects нет данных, rule возвращает пустой list, score = 0."""
        register_rule(BirthYearMatchRule())
        hypothesis = compose_hypothesis(
            hypothesis_type=HypothesisType.SAME_PERSON,
            subject_a={"given": "Anon"},
            subject_b={"given": "Other"},
        )
        assert hypothesis.evidences == []
        assert hypothesis.composite_score == 0.0
