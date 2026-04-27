"""Тесты Levenshtein / token-set / weighted_score."""

from __future__ import annotations

import pytest
from entity_resolution.string_matching import (
    levenshtein_ratio,
    token_set_ratio,
    weighted_score,
)


class TestLevenshteinRatio:
    def test_identical_strings_return_one(self) -> None:
        assert levenshtein_ratio("Smith", "Smith") == 1.0

    def test_case_insensitive(self) -> None:
        assert levenshtein_ratio("Smith", "smith") == 1.0

    def test_completely_different_strings(self) -> None:
        score = levenshtein_ratio("foo", "xyz")
        assert 0.0 <= score <= 0.5

    def test_both_empty_returns_one(self) -> None:
        assert levenshtein_ratio("", "") == 1.0

    def test_one_empty_returns_zero(self) -> None:
        assert levenshtein_ratio("Smith", "") == 0.0
        assert levenshtein_ratio("", "Smith") == 0.0

    def test_minor_typo_high_ratio(self) -> None:
        score = levenshtein_ratio("Zhitnitzky", "Zhitnitsky")
        assert score >= 0.85


class TestTokenSetRatio:
    def test_same_tokens_different_order(self) -> None:
        score = token_set_ratio("apple banana cherry", "cherry apple banana")
        assert score == 1.0

    def test_subset_tokens_high_score(self) -> None:
        score = token_set_ratio("Slonim Grodno", "Slonim")
        assert score >= 0.5  # token_set_ratio handles subsets generously

    def test_empty_strings(self) -> None:
        assert token_set_ratio("", "") == 1.0
        assert token_set_ratio("foo", "") == 0.0


class TestWeightedScore:
    def test_simple_weighted_average(self) -> None:
        scores = {"a": 1.0, "b": 0.5}
        weights = {"a": 0.5, "b": 0.5}
        assert weighted_score(scores, weights) == pytest.approx(0.75)

    def test_unequal_weights(self) -> None:
        scores = {"a": 1.0, "b": 0.0}
        weights = {"a": 0.8, "b": 0.2}
        assert weighted_score(scores, weights) == pytest.approx(0.8)

    def test_missing_component_redistributes_weight(self) -> None:
        """Ключ отсутствует в scores → пропускается, веса не штрафуют."""
        scores = {"a": 1.0}  # 'b' отсутствует
        weights = {"a": 0.5, "b": 0.5}
        # Только 'a' учитывается, total_weight = 0.5, sum = 0.5 → 1.0
        assert weighted_score(scores, weights) == pytest.approx(1.0)

    def test_no_known_components_returns_zero(self) -> None:
        scores: dict[str, float] = {}
        weights = {"a": 0.5, "b": 0.5}
        assert weighted_score(scores, weights) == 0.0

    def test_weights_dont_have_to_sum_to_one(self) -> None:
        scores = {"a": 1.0, "b": 0.5}
        weights = {"a": 1.0, "b": 1.0}  # сумма 2.0
        # Нормализуется внутри: (1.0*1 + 0.5*1) / 2 = 0.75
        assert weighted_score(scores, weights) == pytest.approx(0.75)
