"""Tests for :class:`entity_resolution.names.PatronymicParser` (Phase 15.10)."""

from __future__ import annotations

import pytest
from entity_resolution.names import ParsedName, PatronymicParser


class TestRussian:
    @pytest.fixture
    def parser(self) -> PatronymicParser:
        return PatronymicParser("ru")

    def test_three_token_classic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Иван Иванович Петров")
        assert result.given == "Иван"
        assert result.patronymic == "Иванович"
        assert result.surname == "Петров"

    def test_female_form(self, parser: PatronymicParser) -> None:
        result = parser.parse("Анна Ивановна Петрова")
        assert result.given == "Анна"
        assert result.patronymic == "Ивановна"
        assert result.surname == "Петрова"

    def test_two_tokens(self, parser: PatronymicParser) -> None:
        result = parser.parse("Иван Петров")
        assert result.given == "Иван"
        assert result.patronymic is None
        assert result.surname == "Петров"

    def test_single_token_surname_heuristic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Петров")
        assert result.surname == "Петров"
        assert result.given is None

    def test_single_token_given_heuristic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Иван")
        assert result.given == "Иван"
        assert result.surname is None

    def test_compound_surname_no_patronymic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Иван Петров Сидоров")
        # Middle (Петров) — surname-suffix, не patronymic suffix → склеиваем.
        assert result.given == "Иван"
        assert result.patronymic is None
        assert result.surname == "Петров Сидоров"

    def test_four_tokens_with_patronymic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Анна Мария Ивановна Петрова")
        assert result.given == "Анна"
        assert result.patronymic == "Ивановна"
        # «Мария» middle-name склеивается с surname.
        assert result.surname is not None
        assert "Петрова" in result.surname

    def test_empty_input(self, parser: PatronymicParser) -> None:
        assert parser.parse("") == ParsedName(given=None, patronymic=None, surname=None, raw="")

    def test_whitespace_only(self, parser: PatronymicParser) -> None:
        result = parser.parse("   ")
        assert result.given is None
        assert result.patronymic is None
        assert result.surname is None


class TestLatinTransliterated:
    @pytest.fixture
    def parser(self) -> PatronymicParser:
        return PatronymicParser("ru")

    def test_latin_male(self, parser: PatronymicParser) -> None:
        result = parser.parse("Ivan Ivanovich Petrov")
        assert result.given == "Ivan"
        assert result.patronymic == "Ivanovich"
        assert result.surname == "Petrov"

    def test_latin_female(self, parser: PatronymicParser) -> None:
        result = parser.parse("Anna Ivanovna Petrova")
        assert result.given == "Anna"
        assert result.patronymic == "Ivanovna"
        assert result.surname == "Petrova"


class TestPolish:
    @pytest.fixture
    def parser(self) -> PatronymicParser:
        return PatronymicParser("pl")

    def test_two_token_no_patronymic(self, parser: PatronymicParser) -> None:
        result = parser.parse("Adam Kowalski")
        assert result.given == "Adam"
        assert result.surname == "Kowalski"
        # Polish-современный — patronymic не выводится.
        assert result.patronymic is None

    def test_female_kowalska(self, parser: PatronymicParser) -> None:
        result = parser.parse("Maria Kowalska")
        assert result.given == "Maria"
        assert result.surname == "Kowalska"
        assert result.patronymic is None

    def test_single_polish_surname(self, parser: PatronymicParser) -> None:
        result = parser.parse("Wojciechowski")
        # -ski суффикс → surname-эвристика.
        assert result.surname == "Wojciechowski"
        assert result.given is None


class TestUkrainianAndBelarusian:
    def test_ukrainian_three_token(self) -> None:
        parser = PatronymicParser("uk")
        result = parser.parse("Олег Іванович Шевченко")
        assert result.given == "Олег"
        assert result.patronymic == "Іванович"
        assert result.surname == "Шевченко"

    def test_belarusian_three_token(self) -> None:
        parser = PatronymicParser("by")
        result = parser.parse("Янка Якаўлевіч Купала")
        assert result.given == "Янка"
        # «Якаўлевіч» — by-male patronymic suffix `евiч` (latin-i вариант).
        # У нас в табличке тарашкевица `евіч` (Cyrillic-і) — narkamauka
        # form может быть `евіч` (Cyrillic) — both supported.
        assert result.patronymic in ("Якаўлевіч",)
        assert result.surname == "Купала"
