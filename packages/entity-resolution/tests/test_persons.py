"""Тесты person_match_score (ADR-0015 §«Persons»)."""

from __future__ import annotations

import pytest
from entity_resolution.persons import PersonForMatching, person_match_score


def _person(
    *,
    given: str | None = "John",
    surname: str | None = "Smith",
    birth_year: int | None = 1850,
    death_year: int | None = None,
    birth_place: str | None = "Slonim",
    sex: str | None = "M",
) -> PersonForMatching:
    return PersonForMatching(
        given=given,
        surname=surname,
        birth_year=birth_year,
        death_year=death_year,
        birth_place=birth_place,
        sex=sex,
    )


class TestPersonMatchScore:
    def test_identical_persons_score_one(self) -> None:
        a = _person()
        score, components = person_match_score(a, a)
        assert score == pytest.approx(1.0)
        assert components["phonetic"] == 1.0
        assert components["name_levenshtein"] == 1.0

    def test_person_dedup_full_match(self) -> None:
        """Two persons with same data → composite ≥ 0.95."""
        a = _person(given="Meir", surname="Zhitnitzky", birth_year=1850)
        b = _person(given="Meir", surname="Zhitnitzky", birth_year=1850)
        score, _ = person_match_score(a, b)
        assert score >= 0.95

    def test_person_dedup_with_transliterated_surname(self) -> None:
        """Главный success signal: Zhitnitzky / Zhytnicki same person."""
        a = _person(given="Meir", surname="Zhitnitzky", birth_year=1850)
        b = _person(given="Meir", surname="Zhytnicki", birth_year=1850)
        score, components = person_match_score(a, b)
        assert score >= 0.80, f"expected ≥0.80, got {score}, components={components}"
        # Phonetic должен совпасть благодаря DM-bucket overlap.
        assert components["phonetic"] == 1.0

    def test_sex_mismatch_returns_zero(self) -> None:
        """Hard rule: оба пола известны и разные → 0.0."""
        a = _person(given="John", surname="Smith", sex="M")
        b = _person(given="John", surname="Smith", sex="F")
        score, components = person_match_score(a, b)
        assert score == 0.0
        assert components == {}

    def test_one_unknown_sex_does_not_filter(self) -> None:
        """Один пол U → не отбрасываем, идёт обычный scoring."""
        a = _person(sex="U")
        b = _person(sex="M")
        score, _ = person_match_score(a, b)
        assert score > 0.0

    def test_birth_year_close_lowers_year_component(self) -> None:
        """±1 год → year_score 0.7, не 1.0."""
        a = _person(birth_year=1850)
        b = _person(birth_year=1851)
        _, components = person_match_score(a, b)
        assert components["birth_year"] == pytest.approx(0.7)

    def test_birth_year_far_year_component_zero(self) -> None:
        """|Δ| > 2 → year_score 0.0."""
        a = _person(birth_year=1850)
        b = _person(birth_year=1880)
        _, components = person_match_score(a, b)
        assert components["birth_year"] == 0.0

    def test_missing_birth_year_component_omitted(self) -> None:
        """birth_year отсутствует у одной из персон → компонент пропускается."""
        a = _person(birth_year=None)
        b = _person(birth_year=1850)
        _, components = person_match_score(a, b)
        assert "birth_year" not in components

    def test_missing_birth_place_component_omitted(self) -> None:
        a = _person(birth_place=None)
        b = _person(birth_place="Slonim")
        _, components = person_match_score(a, b)
        assert "birth_place" not in components

    def test_components_include_breakdown(self) -> None:
        """UI Phase 4.5 нужен покомпонентный breakdown."""
        a = _person(given="Meir", surname="Zhitnitzky", birth_year=1850, birth_place="Slonim")
        b = _person(given="Meir", surname="Zhitnitsky", birth_year=1850, birth_place="Slonim")
        _, components = person_match_score(a, b)
        assert "phonetic" in components
        assert "name_levenshtein" in components
        assert "birth_year" in components
        assert "birth_place" in components

    def test_no_phonetic_match_lowers_score(self) -> None:
        """Совершенно разные surname'ы → low score даже при остальных совпадениях."""
        a = _person(given="John", surname="Smith")
        b = _person(given="John", surname="Zhitnitzky")
        score, _ = person_match_score(a, b)
        assert score < 0.80
