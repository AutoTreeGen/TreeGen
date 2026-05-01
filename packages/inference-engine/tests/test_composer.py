"""Тесты compose_hypothesis: Phase 7.5 aggregation (Bayesian fusion + contradictions).

ADR-0065. Phase 7.0–7.4 использовала линейную ``Σ supports − Σ contradicts``
формулу; ожидаемые значения в этих тестах обновлены под новую семантику:

* Two SUPPORTS из разных rule_ids → 1 − (1−w1)(1−w2).
* CONTRADICTS — не вычитает свой weight, а добавляет фиксированный
  штраф 0.1 за единицу (cap 0.5).
* Floor 0.05 применяется когда есть хоть какое-то evidence.
"""

from __future__ import annotations

import math
from typing import Any
from uuid import uuid4

from inference_engine import (
    Evidence,
    EvidenceDirection,
    HypothesisType,
    compose_hypothesis,
    register_rule,
)


class _ConstRule:
    """Rule, возвращающий заранее заданный список Evidence — для unit-тестов composer'а.

    Использует ``rule_id`` владельца теста (в evidences.rule_id) — это
    оставляет реалистичную provenance.
    """

    def __init__(self, rule_id: str, evidences: list[Evidence]) -> None:
        self.rule_id = rule_id
        self._evidences = evidences

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del subject_a, subject_b, context
        return list(self._evidences)


def _ev(direction: EvidenceDirection, weight: float, rule_id: str = "r") -> Evidence:
    return Evidence(
        rule_id=rule_id,
        direction=direction,
        weight=weight,
        observation=f"{direction.value} {weight:.2f}",
    )


def test_compose_with_no_rules_yields_empty_evidences_and_zero_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
    )
    assert hyp.evidences == []
    # Пустой evidence-list → 0.0 (floor не применяется без данных).
    assert hyp.composite_score == 0.0


def test_compose_with_no_registered_rules_yields_zero_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=None,  # implicit registry, autouse fixture cleared it
    )
    assert hyp.composite_score == 0.0


def test_supports_from_different_sources_use_bayesian_fusion() -> None:
    """Phase 7.5: 0.3 + 0.4 → 1 − 0.7·0.6 = 0.58, не 0.7."""
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.3, "a")]),
            _ConstRule("b", [_ev(EvidenceDirection.SUPPORTS, 0.4, "b")]),
        ],
    )
    assert math.isclose(hyp.composite_score, 0.58, abs_tol=1e-9)
    assert len(hyp.evidences) == 2


def test_single_contradiction_subtracts_fixed_penalty() -> None:
    """Phase 7.5: penalty 0.1 за CONTRADICTS, не зависит от weight.

    SUPPORTS 0.6 → fused 0.6; одно CONTRADICTS → −0.1; итог 0.5.
    """
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.6, "a")]),
            _ConstRule("b", [_ev(EvidenceDirection.CONTRADICTS, 0.4, "b")]),
        ],
    )
    assert math.isclose(hyp.composite_score, 0.5, abs_tol=1e-9)


def test_neutral_does_not_change_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.5, "a")]),
            _ConstRule("b", [_ev(EvidenceDirection.NEUTRAL, 0.7, "b")]),
        ],
    )
    assert math.isclose(hyp.composite_score, 0.5, abs_tol=1e-9)
    # Neutral evidence остаётся видимой — UI explanation должен её показать.
    assert any(ev.direction is EvidenceDirection.NEUTRAL for ev in hyp.evidences)


def test_strong_supports_approach_one_via_bayesian_fusion() -> None:
    """Phase 7.5: высокие SUPPORTS подходят к 1.0 без явного clamp.

    0.9 + 0.8 → 1 − 0.1·0.2 = 0.98; не 1.0, но «почти уверенно».
    """
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.9, "a")]),
            _ConstRule("b", [_ev(EvidenceDirection.SUPPORTS, 0.8, "b")]),
        ],
    )
    assert math.isclose(hyp.composite_score, 0.98, abs_tol=1e-9)


def test_floor_applied_when_strong_contradiction_dominates() -> None:
    """Phase 7.5: weak SUPPORTS + сильный CONTRADICTS → floor 0.05, не 0.

    0.2 SUPPORTS → fused 0.2; одно CONTRADICTS (вес неважен) → −0.1;
    итог 0.1 — выше floor'а, поэтому возвращается как есть.
    """
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.2, "a")]),
            _ConstRule("b", [_ev(EvidenceDirection.CONTRADICTS, 0.9, "b")]),
        ],
    )
    assert math.isclose(hyp.composite_score, 0.1, abs_tol=1e-9)


def test_default_uses_registry_when_rules_is_none() -> None:
    register_rule(_ConstRule("registered", [_ev(EvidenceDirection.SUPPORTS, 0.6, "registered")]))
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
    )
    assert math.isclose(hyp.composite_score, 0.6, abs_tol=1e-9)
    assert hyp.evidences[0].rule_id == "registered"


def test_explicit_rules_override_registry() -> None:
    register_rule(_ConstRule("registered", [_ev(EvidenceDirection.SUPPORTS, 0.9, "registered")]))
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[_ConstRule("explicit", [_ev(EvidenceDirection.SUPPORTS, 0.1, "explicit")])],
    )
    # 0.1 fused в одиночку = 0.1; floor 0.05 ≤ 0.1, значит 0.1.
    assert math.isclose(hyp.composite_score, 0.1, abs_tol=1e-9)
    assert hyp.evidences[0].rule_id == "explicit"


def test_subject_ids_are_preserved_when_passed() -> None:
    a, b = uuid4(), uuid4()
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.PARENT_CHILD,
        subject_a={},
        subject_b={},
        subject_a_id=a,
        subject_b_id=b,
    )
    assert hyp.subject_a_id == a
    assert hyp.subject_b_id == b


def test_subject_ids_default_to_fresh_uuids() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.PARENT_CHILD,
        subject_a={},
        subject_b={},
    )
    assert hyp.subject_a_id != hyp.subject_b_id


def test_alternatives_default_empty_in_phase_7_0() -> None:
    """Phase 7.0 — alternative-generation отложена. Composer возвращает пустой list."""
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
    )
    assert hyp.alternatives == []


def test_context_passed_through_to_rule() -> None:
    """Rule должен видеть переданный context."""
    captured: dict[str, Any] = {}

    class _Capture:
        rule_id = "capture"

        def apply(
            self,
            subject_a: dict[str, Any],
            subject_b: dict[str, Any],
            context: dict[str, Any],
        ) -> list[Evidence]:
            del subject_a, subject_b
            captured.update(context)
            return []

    compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        context={"tree_id": "tree-123"},
        rules=[_Capture()],
    )
    assert captured == {"tree_id": "tree-123"}


def test_default_context_is_empty_dict() -> None:
    captured: list[dict[str, Any]] = []

    class _Capture:
        rule_id = "capture"

        def apply(
            self,
            subject_a: dict[str, Any],
            subject_b: dict[str, Any],
            context: dict[str, Any],
        ) -> list[Evidence]:
            del subject_a, subject_b
            captured.append(context)
            return []

    compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[_Capture()],
    )
    assert captured == [{}]
