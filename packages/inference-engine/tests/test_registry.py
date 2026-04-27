"""Тесты module-level rule registry."""

from __future__ import annotations

from typing import Any

import pytest
from inference_engine import (
    Evidence,
    EvidenceDirection,
    InferenceRule,
    RuleAlreadyRegisteredError,
    RuleNotFoundError,
    all_rules,
    get_rule,
    register_rule,
    unregister_rule,
)


class _Rule:
    """Минимальный rule, удовлетворяющий InferenceRule Protocol."""

    def __init__(self, rule_id: str = "stub") -> None:
        self.rule_id = rule_id

    def apply(
        self,
        subject_a: dict[str, Any],
        subject_b: dict[str, Any],
        context: dict[str, Any],
    ) -> list[Evidence]:
        del subject_a, subject_b, context
        return [
            Evidence(
                rule_id=self.rule_id,
                direction=EvidenceDirection.NEUTRAL,
                weight=0.0,
                observation="stub",
            )
        ]


def test_protocol_isinstance_check_accepts_compliant_class() -> None:
    """runtime_checkable Protocol должен признавать структурно подходящие объекты."""
    assert isinstance(_Rule(), InferenceRule)


def test_protocol_isinstance_check_rejects_missing_attrs() -> None:
    class _NotARule:
        pass

    assert not isinstance(_NotARule(), InferenceRule)


def test_register_and_lookup_rule() -> None:
    rule = _Rule("alpha")
    register_rule(rule)
    assert get_rule("alpha") is rule


def test_register_non_rule_raises_type_error() -> None:
    with pytest.raises(TypeError):
        register_rule(object())  # type: ignore[arg-type]


def test_duplicate_register_raises() -> None:
    register_rule(_Rule("x"))
    with pytest.raises(RuleAlreadyRegisteredError):
        register_rule(_Rule("x"))


def test_get_rule_missing_raises() -> None:
    with pytest.raises(RuleNotFoundError):
        get_rule("does-not-exist")


def test_all_rules_preserves_insertion_order() -> None:
    register_rule(_Rule("a"))
    register_rule(_Rule("b"))
    register_rule(_Rule("c"))
    assert [r.rule_id for r in all_rules()] == ["a", "b", "c"]


def test_unregister_removes_rule() -> None:
    register_rule(_Rule("x"))
    unregister_rule("x")
    with pytest.raises(RuleNotFoundError):
        get_rule("x")


def test_unregister_unknown_is_noop() -> None:
    unregister_rule("never-registered")  # не должно бросать


def test_clear_registry_via_autouse_fixture() -> None:
    """Autouse фикстура из conftest должна оставить registry пустым на старте теста."""
    assert all_rules() == []
