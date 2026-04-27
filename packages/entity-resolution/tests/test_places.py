"""Тесты place_match_score (ADR-0015 §«Places»)."""

from __future__ import annotations

from entity_resolution.places import place_match_score


class TestPlaceMatchScore:
    def test_identical_places_score_one(self) -> None:
        assert place_match_score("Slonim", "Slonim") == 1.0

    def test_place_dedup_slonim_with_region(self) -> None:
        """Главный success signal: «Slonim» vs «Slonim, Grodno» ≥ 0.80."""
        score = place_match_score("Slonim", "Slonim, Grodno")
        assert score >= 0.80, f"expected ≥0.80, got {score}"

    def test_token_prefix_subset_gets_boost(self) -> None:
        """«Slonim» как token-prefix «Slonim, Grodno, Russian Empire»."""
        score = place_match_score("Slonim", "Slonim, Grodno, Russian Empire")
        assert score >= 0.80

    def test_case_insensitive(self) -> None:
        assert place_match_score("Slonim", "SLONIM") == 1.0

    def test_completely_different_places_low_score(self) -> None:
        score = place_match_score("Slonim, Belarus", "Boston, Massachusetts")
        assert score < 0.5

    def test_score_capped_at_one(self) -> None:
        """Boost не должен поднять score выше 1.0."""
        score = place_match_score("Slonim", "Slonim")
        assert score <= 1.0

    def test_different_regions_same_city(self) -> None:
        """Slonim, Grodno vs Slonim, Belarus — оба про Slonim, разные регионы."""
        score = place_match_score("Slonim, Grodno", "Slonim, Belarus")
        # Token «slonim» совпадает, второй token разный — должен быть high
        # средний: token_set_ratio считает «Slonim» в обоих, и хоть второй
        # токен разный, общий score остаётся приемлемым.
        assert score >= 0.5

    def test_empty_strings(self) -> None:
        assert place_match_score("", "") == 1.0
        assert place_match_score("Slonim", "") == 0.0
