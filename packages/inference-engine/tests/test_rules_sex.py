"""Тесты SexConsistencyRule (Phase 7.1 Task 3)."""

from __future__ import annotations

from inference_engine.rules.sex import SexConsistencyRule
from inference_engine.types import EvidenceDirection

_RULE = SexConsistencyRule()


def test_rule_id_is_stable() -> None:
    assert _RULE.rule_id == "sex_consistency"


def test_mismatch_in_same_person_contradicts() -> None:
    out = _RULE.apply(
        {"sex": "M"},
        {"sex": "F"},
        {"hypothesis_type": "same_person"},
    )
    assert len(out) == 1
    assert out[0].direction is EvidenceDirection.CONTRADICTS
    assert out[0].weight == 0.95


def test_match_in_same_person_no_evidence() -> None:
    """Совпадение пола — не выдаёт SUPPORTS (это слабый сигнал, оставляем
    другим rule's). Только mismatch → CONTRADICTS, тождество → silent."""
    out = _RULE.apply(
        {"sex": "M"},
        {"sex": "M"},
        {"hypothesis_type": "same_person"},
    )
    assert out == []


def test_mismatch_in_marriage_no_evidence() -> None:
    """Для marriage hypothesis разный пол — норма, никакого contradicts."""
    out = _RULE.apply(
        {"sex": "M"},
        {"sex": "F"},
        {"hypothesis_type": "marriage"},
    )
    assert out == []


def test_mismatch_in_parent_child_no_evidence() -> None:
    """Parent_child hypothesis: разный пол не противоречит."""
    out = _RULE.apply(
        {"sex": "M"},
        {"sex": "F"},
        {"hypothesis_type": "parent_child"},
    )
    assert out == []


def test_mismatch_no_hypothesis_type_no_evidence() -> None:
    """Без hypothesis_type в context — silent (consérvative)."""
    out = _RULE.apply({"sex": "M"}, {"sex": "F"}, {})
    assert out == []


def test_unknown_sex_no_evidence() -> None:
    """U / X / None — не триггерит CONTRADICTS."""
    out = _RULE.apply(
        {"sex": "U"},
        {"sex": "M"},
        {"hypothesis_type": "same_person"},
    )
    assert out == []
    out = _RULE.apply(
        {"sex": None},
        {"sex": "F"},
        {"hypothesis_type": "same_person"},
    )
    assert out == []
    out = _RULE.apply(
        {"sex": "X"},
        {"sex": "M"},
        {"hypothesis_type": "same_person"},
    )
    assert out == []


def test_observation_includes_values() -> None:
    out = _RULE.apply(
        {"sex": "M"},
        {"sex": "F"},
        {"hypothesis_type": "same_person"},
    )
    assert "M" in out[0].observation
    assert "F" in out[0].observation
