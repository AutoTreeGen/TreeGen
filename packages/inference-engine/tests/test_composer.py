"""Тесты compose_hypothesis: weighted-sum формула, edge cases, registry-fallback."""

from __future__ import annotations

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
    assert hyp.composite_score == 0.0


def test_compose_with_no_registered_rules_yields_zero_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=None,  # implicit registry, autouse fixture cleared it
    )
    assert hyp.composite_score == 0.0


def test_supports_increase_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.3)]),
            _ConstRule("b", [_ev(EvidenceDirection.SUPPORTS, 0.4)]),
        ],
    )
    assert hyp.composite_score == 0.7
    assert len(hyp.evidences) == 2


def test_contradicts_decrease_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.6)]),
            _ConstRule("b", [_ev(EvidenceDirection.CONTRADICTS, 0.4)]),
        ],
    )
    assert abs(hyp.composite_score - 0.2) < 1e-9


def test_neutral_does_not_change_score() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.5)]),
            _ConstRule("b", [_ev(EvidenceDirection.NEUTRAL, 0.7)]),
        ],
    )
    assert hyp.composite_score == 0.5
    # Neutral evidence остаётся видимой — UI explanation должен её показать.
    assert any(ev.direction is EvidenceDirection.NEUTRAL for ev in hyp.evidences)


def test_score_clamped_to_one_when_supports_overflow() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.9)]),
            _ConstRule("b", [_ev(EvidenceDirection.SUPPORTS, 0.8)]),
        ],
    )
    assert hyp.composite_score == 1.0


def test_score_clamped_to_zero_when_contradicts_overflow() -> None:
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[
            _ConstRule("a", [_ev(EvidenceDirection.SUPPORTS, 0.2)]),
            _ConstRule("b", [_ev(EvidenceDirection.CONTRADICTS, 0.9)]),
        ],
    )
    assert hyp.composite_score == 0.0


def test_default_uses_registry_when_rules_is_none() -> None:
    register_rule(_ConstRule("registered", [_ev(EvidenceDirection.SUPPORTS, 0.6, "registered")]))
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
    )
    assert hyp.composite_score == 0.6
    assert hyp.evidences[0].rule_id == "registered"


def test_explicit_rules_override_registry() -> None:
    register_rule(_ConstRule("registered", [_ev(EvidenceDirection.SUPPORTS, 0.9, "registered")]))
    hyp = compose_hypothesis(
        hypothesis_type=HypothesisType.SAME_PERSON,
        subject_a={},
        subject_b={},
        rules=[_ConstRule("explicit", [_ev(EvidenceDirection.SUPPORTS, 0.1, "explicit")])],
    )
    assert hyp.composite_score == 0.1
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
