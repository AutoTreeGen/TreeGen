"""Тесты для confidence aggregation v2 (Phase 7.5, ADR-0065).

Покрытие:

* Edge cases: пустой list, одно evidence, только NEUTRAL, только
  CONTRADICTS, mixed.
* Bayesian fusion для разных rule_id.
* Same-source corroboration: одинаковый rule_id → weighted average,
  не fusion (важно: иначе naïve fusion дублирует доказательство).
* Contradiction penalty: 0.1 за штуку, cap 0.5.
* Floor 0.05 при наличии evidence.

Property tests (hypothesis library):

* Composite всегда в ``[0, 1]``.
* Adding more SUPPORTS evidence не уменьшает composite (monotonicity).
* Adding CONTRADICTS не увеличивает composite.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from inference_engine import Evidence, EvidenceDirection
from inference_engine.aggregation import (
    CONTRADICTION_PENALTY_CAP,
    CONTRADICTION_PENALTY_PER_EVIDENCE,
    MIN_CONFIDENCE_WITH_EVIDENCE,
    AggregatedConfidence,
    aggregate_confidence,
)


def _ev(
    direction: EvidenceDirection,
    weight: float = 0.5,
    rule_id: str = "rule-x",
) -> Evidence:
    return Evidence(
        rule_id=rule_id,
        direction=direction,
        weight=weight,
        observation=f"{direction.value} {weight:.2f}",
    )


# -----------------------------------------------------------------------------
# Edge cases
# -----------------------------------------------------------------------------


def test_empty_evidence_list_yields_zero() -> None:
    """Пустой list → 0.0; floor НЕ применяется (нет данных вообще)."""
    result = aggregate_confidence([])
    assert result.composite_score == 0.0
    assert result.source_breakdown == []
    assert result.contradiction_flags == []
    assert result.contradiction_penalty == 0.0


def test_single_supports_evidence_passes_through() -> None:
    """Одно SUPPORTS-evidence → composite = его weight (1 − (1 − w) = w)."""
    result = aggregate_confidence([_ev(EvidenceDirection.SUPPORTS, 0.7)])
    assert math.isclose(result.composite_score, 0.7, abs_tol=1e-9)
    assert len(result.source_breakdown) == 1
    assert result.contradiction_flags == []


def test_only_neutral_evidence_yields_floor() -> None:
    """Только NEUTRAL → composite не двигается, но floor 0.05 применяется."""
    result = aggregate_confidence([_ev(EvidenceDirection.NEUTRAL, 0.5)])
    assert result.composite_score == MIN_CONFIDENCE_WITH_EVIDENCE
    assert result.source_breakdown == []  # NEUTRAL не попадает в breakdown
    assert result.contradiction_flags == []


def test_only_contradicts_yields_floor() -> None:
    """Только CONTRADICTS → 0 (без supports) − penalty, но floor 0.05."""
    result = aggregate_confidence([_ev(EvidenceDirection.CONTRADICTS, 0.5, "r")])
    assert result.composite_score == MIN_CONFIDENCE_WITH_EVIDENCE
    assert result.contradiction_flags == ["r"]
    assert math.isclose(
        result.contradiction_penalty,
        CONTRADICTION_PENALTY_PER_EVIDENCE,
        abs_tol=1e-9,
    )


# -----------------------------------------------------------------------------
# Bayesian fusion across independent sources
# -----------------------------------------------------------------------------


def test_two_independent_supports_use_bayesian_fusion() -> None:
    """0.5 + 0.5 → 1 − 0.5·0.5 = 0.75 (не 1.0, не 0.5)."""
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.5, "rule-a"),
            _ev(EvidenceDirection.SUPPORTS, 0.5, "rule-b"),
        ]
    )
    assert math.isclose(result.composite_score, 0.75, abs_tol=1e-9)
    assert len(result.source_breakdown) == 2


def test_three_independent_supports_continue_fusion() -> None:
    """0.5 + 0.5 + 0.5 → 1 − 0.125 = 0.875."""
    result = aggregate_confidence(
        [_ev(EvidenceDirection.SUPPORTS, 0.5, f"rule-{i}") for i in range(3)]
    )
    assert math.isclose(result.composite_score, 0.875, abs_tol=1e-9)


def test_one_strong_one_weak_support() -> None:
    """0.9 + 0.2 → 1 − 0.1·0.8 = 0.92."""
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.9, "strong"),
            _ev(EvidenceDirection.SUPPORTS, 0.2, "weak"),
        ]
    )
    assert math.isclose(result.composite_score, 0.92, abs_tol=1e-9)


# -----------------------------------------------------------------------------
# Same-source corroboration
# -----------------------------------------------------------------------------


def test_same_source_corroboration_uses_average_not_fusion() -> None:
    """Два evidence одного rule_id → среднее (0.6), не fusion (≈0.84).

    Critical: naive Bayesian fusion на одном источнике переоценивает.
    «Surname matched twice» ≠ «surname + birth-year».
    """
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.4, "surname"),
            _ev(EvidenceDirection.SUPPORTS, 0.8, "surname"),
        ]
    )
    assert math.isclose(result.composite_score, 0.6, abs_tol=1e-9)
    assert len(result.source_breakdown) == 1
    assert result.source_breakdown[0].rule_id == "surname"
    assert result.source_breakdown[0].evidence_count == 2


def test_mixed_same_source_and_independent() -> None:
    """Группа same-source усредняется, потом fusion с независимым источником.

    surname: [0.4, 0.8] → avg 0.6.
    birth-year: [0.5] → 0.5.
    Fusion: 1 − 0.4·0.5 = 0.8.
    """
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.4, "surname"),
            _ev(EvidenceDirection.SUPPORTS, 0.8, "surname"),
            _ev(EvidenceDirection.SUPPORTS, 0.5, "birth-year"),
        ]
    )
    assert math.isclose(result.composite_score, 0.8, abs_tol=1e-9)
    assert len(result.source_breakdown) == 2


# -----------------------------------------------------------------------------
# Contradictions
# -----------------------------------------------------------------------------


def test_one_contradiction_subtracts_zero_one() -> None:
    """SUPPORTS 0.7 − one CONTRADICTS = 0.6."""
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.7, "a"),
            _ev(EvidenceDirection.CONTRADICTS, 0.4, "b"),
        ]
    )
    assert math.isclose(result.composite_score, 0.6, abs_tol=1e-9)
    assert result.contradiction_flags == ["b"]


def test_contradiction_penalty_caps_at_half() -> None:
    """8 CONTRADICTS → penalty cap 0.5, не 0.8."""
    contradicts = [_ev(EvidenceDirection.CONTRADICTS, 0.5, f"r-{i}") for i in range(8)]
    supports = [_ev(EvidenceDirection.SUPPORTS, 0.9, "strong")]
    result = aggregate_confidence(supports + contradicts)
    # 0.9 fused (одна группа) − 0.5 cap = 0.4.
    assert math.isclose(result.composite_score, 0.4, abs_tol=1e-9)
    assert math.isclose(
        result.contradiction_penalty,
        CONTRADICTION_PENALTY_CAP,
        abs_tol=1e-9,
    )
    assert len(result.contradiction_flags) == 8


def test_contradiction_penalty_independent_of_weight() -> None:
    """Penalty фиксированный — два CONTRADICTS на 0.1 и 0.99 дают одинаковые −0.1 каждое."""
    weak = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.7, "s"),
            _ev(EvidenceDirection.CONTRADICTS, 0.1, "c"),
        ]
    )
    strong = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.7, "s"),
            _ev(EvidenceDirection.CONTRADICTS, 0.99, "c"),
        ]
    )
    assert math.isclose(weak.composite_score, strong.composite_score, abs_tol=1e-9)


# -----------------------------------------------------------------------------
# Floor
# -----------------------------------------------------------------------------


def test_floor_keeps_score_above_zero_when_evidence_exists() -> None:
    """Слабый supports + cap'нутые contradicts → floor 0.05, не 0."""
    contradicts = [_ev(EvidenceDirection.CONTRADICTS, 0.5, f"r-{i}") for i in range(10)]
    supports = [_ev(EvidenceDirection.SUPPORTS, 0.05, "weak")]
    result = aggregate_confidence(supports + contradicts)
    # 0.05 fused − 0.5 cap = негативное, clip в 0, floor → 0.05.
    assert math.isclose(
        result.composite_score,
        MIN_CONFIDENCE_WITH_EVIDENCE,
        abs_tol=1e-9,
    )


# -----------------------------------------------------------------------------
# Range / structural invariants
# -----------------------------------------------------------------------------


def test_composite_score_always_in_unit_interval_for_typical_inputs() -> None:
    cases: list[list[Evidence]] = [
        [_ev(EvidenceDirection.SUPPORTS, 1.0, "a"), _ev(EvidenceDirection.SUPPORTS, 1.0, "b")],
        [_ev(EvidenceDirection.SUPPORTS, 0.0, "a")],
        [_ev(EvidenceDirection.CONTRADICTS, 1.0, f"r-{i}") for i in range(20)],
    ]
    for evs in cases:
        result = aggregate_confidence(evs)
        assert 0.0 <= result.composite_score <= 1.0


def test_source_breakdown_sorted_by_weight_desc() -> None:
    result = aggregate_confidence(
        [
            _ev(EvidenceDirection.SUPPORTS, 0.3, "low"),
            _ev(EvidenceDirection.SUPPORTS, 0.9, "high"),
            _ev(EvidenceDirection.SUPPORTS, 0.6, "mid"),
        ]
    )
    weights = [c.aggregated_weight for c in result.source_breakdown]
    assert weights == sorted(weights, reverse=True)
    assert result.source_breakdown[0].rule_id == "high"


def test_aggregated_confidence_is_frozen_pydantic() -> None:
    """AggregatedConfidence — immutable: попытки мутировать падают."""
    from pydantic import ValidationError

    result = aggregate_confidence([_ev(EvidenceDirection.SUPPORTS, 0.5)])
    with pytest.raises(ValidationError, match="frozen"):
        result.composite_score = 0.9  # type: ignore[misc]


# -----------------------------------------------------------------------------
# Property-based tests (hypothesis library)
# -----------------------------------------------------------------------------


_evidence_strategy = st.builds(
    Evidence,
    rule_id=st.sampled_from(["rule-a", "rule-b", "rule-c", "rule-d", "rule-e"]),
    direction=st.sampled_from(list(EvidenceDirection)),
    weight=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    observation=st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""),
)


@given(st.lists(_evidence_strategy, min_size=0, max_size=15))
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_composite_score_in_unit_interval(evidences: list[Evidence]) -> None:
    """Property: composite_score всегда в [0, 1] для любого ввода."""
    result = aggregate_confidence(evidences)
    assert 0.0 <= result.composite_score <= 1.0


@given(st.lists(_evidence_strategy, min_size=0, max_size=15))
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_returns_valid_aggregated_confidence(evidences: list[Evidence]) -> None:
    """Property: AggregatedConfidence Pydantic-валиден для любого ввода."""
    result = aggregate_confidence(evidences)
    assert isinstance(result, AggregatedConfidence)
    # Все contributions имеют валидные weights.
    for c in result.source_breakdown:
        assert 0.0 <= c.aggregated_weight <= 1.0
        assert c.evidence_count >= 1
    # Penalty не превышает cap.
    assert 0.0 <= result.contradiction_penalty <= CONTRADICTION_PENALTY_CAP


@st.composite
def _supports_only_lists(draw: st.DrawFn) -> tuple[list[Evidence], list[Evidence]]:
    """Стратегия: пара (base, base + дополнительное SUPPORTS)."""
    base = draw(
        st.lists(
            st.builds(
                Evidence,
                rule_id=st.sampled_from(["a", "b", "c"]),
                direction=st.just(EvidenceDirection.SUPPORTS),
                weight=st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
                observation=st.just("obs"),
            ),
            min_size=0,
            max_size=10,
        )
    )
    extra = draw(
        st.builds(
            Evidence,
            rule_id=st.sampled_from(["d", "e"]),  # отдельный rule_id чтобы fusion, не avg
            direction=st.just(EvidenceDirection.SUPPORTS),
            weight=st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
            observation=st.just("obs"),
        )
    )
    return base, [*base, extra]


@given(_supports_only_lists())
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_adding_independent_support_is_monotonic(
    pair: tuple[list[Evidence], list[Evidence]],
) -> None:
    """Property: добавление SUPPORTS-evidence из нового rule_id не уменьшает composite."""
    base, extended = pair
    base_score = aggregate_confidence(base).composite_score
    extended_score = aggregate_confidence(extended).composite_score
    # Floor может «поднять» базу до 0.05 если она была 0; в extended всегда
    # будет evidence, поэтому extended_score ≥ base_score.
    assert extended_score >= base_score - 1e-9


@st.composite
def _supports_with_optional_contradiction(
    draw: st.DrawFn,
) -> tuple[list[Evidence], list[Evidence]]:
    base = draw(
        st.lists(
            st.builds(
                Evidence,
                rule_id=st.sampled_from(["a", "b", "c"]),
                direction=st.just(EvidenceDirection.SUPPORTS),
                weight=st.floats(0.1, 1.0, allow_nan=False, allow_infinity=False),
                observation=st.just("obs"),
            ),
            min_size=1,  # хотя бы одно SUPPORTS чтобы было от чего убавлять
            max_size=8,
        )
    )
    contradiction = draw(
        st.builds(
            Evidence,
            rule_id=st.sampled_from(["x", "y"]),
            direction=st.just(EvidenceDirection.CONTRADICTS),
            weight=st.floats(0.0, 1.0, allow_nan=False, allow_infinity=False),
            observation=st.just("obs"),
        )
    )
    return base, [*base, contradiction]


@given(_supports_with_optional_contradiction())
@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_property_adding_contradiction_does_not_increase_score(
    pair: tuple[list[Evidence], list[Evidence]],
) -> None:
    """Property: добавление CONTRADICTS не увеличивает composite_score.

    Caveat: floor 0.05 может «поднять» очень низкий score в extended;
    исключаем тривиальные кейсы где разница меньше epsilon.
    """
    base, extended = pair
    base_score = aggregate_confidence(base).composite_score
    extended_score = aggregate_confidence(extended).composite_score
    # Если base уже у floor — extended тоже у floor; равно, монотонно ✓.
    # Иначе: extended ≤ base (учитываем floor effect через assume).
    assume(base_score > MIN_CONFIDENCE_WITH_EVIDENCE)
    assert extended_score <= base_score + 1e-9


# -----------------------------------------------------------------------------
# Performance smoke-test (target: <1ms per call для bulk compute).
# -----------------------------------------------------------------------------


def test_aggregation_handles_large_evidence_list_quickly() -> None:
    """Smoke-test: 100 evidence на 10 разных rule_ids считается за < 1ms.

    Не строгий benchmark (CI flake-prone), просто sanity check что нет
    O(n²) или quadratic explode.
    """
    import time

    evidences: list[Evidence] = []
    for i in range(100):
        evidences.append(_ev(EvidenceDirection.SUPPORTS, 0.3 + (i % 7) * 0.1, f"rule-{i % 10}"))

    t0 = time.perf_counter()
    for _ in range(100):  # амортизируем измерение
        aggregate_confidence(evidences)
    elapsed_per_call = (time.perf_counter() - t0) / 100

    # Запас в 10× от target (<1ms): на CI железо может быть медленным.
    assert elapsed_per_call < 0.010, f"Too slow: {elapsed_per_call * 1000:.2f}ms per call"
