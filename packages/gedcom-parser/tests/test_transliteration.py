"""Тесты модуля ``gedcom_parser.transliteration``."""

from __future__ import annotations

import pytest
from gedcom_parser.transliteration import is_cyrillic, transliterate_iso9

# -----------------------------------------------------------------------------
# transliterate_iso9 — основные кейсы
# -----------------------------------------------------------------------------


class TestTransliterateIso9:
    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("Иван", "Ivan"),
            ("ИВАН", "IVAN"),
            ("иван", "ivan"),
            ("Петров", "Petrov"),
            ("Слоним", "Slonim"),
        ],
    )
    def test_simple_russian(self, src: str, expected: str) -> None:
        assert transliterate_iso9(src) == expected

    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("Жуков", "Žukov"),
            ("Чехов", "Čehov"),
            ("Шишкин", "Šiškin"),
            ("Щукин", "Ŝukin"),
            ("Ёлкин", "Ëlkin"),
            ("Юрий", "Ûrij"),
            ("Яков", "Âkov"),
            ("Эра", "Èra"),
            ("Цой", "Coj"),
        ],
    )
    def test_diacritic_letters(self, src: str, expected: str) -> None:
        assert transliterate_iso9(src) == expected

    def test_full_phrase(self) -> None:
        # Полная цепочка отчества и фамилии.
        assert transliterate_iso9("Иван Иванович Петров") == "Ivan Ivanovič Petrov"

    def test_hard_and_soft_signs(self) -> None:
        # Ъ → ʺ (modifier double prime), Ь → ʹ (modifier prime).
        out = transliterate_iso9("Подъезд день")
        assert "ʺ" in out
        assert "ʹ" in out

    def test_passthrough_latin_digits_punctuation(self) -> None:
        assert transliterate_iso9("John 1850") == "John 1850"
        assert transliterate_iso9("Иван (1850)") == "Ivan (1850)"
        assert transliterate_iso9("Слоним, Россия!") == "Slonim, Rossiâ!"

    def test_empty(self) -> None:
        assert transliterate_iso9("") == ""

    def test_only_punctuation(self) -> None:
        assert transliterate_iso9("...,?!") == "...,?!"


class TestUkrainianBelarusian:
    def test_ukrainian_letters(self) -> None:
        assert transliterate_iso9("Київ") == "Kiïv"
        # Є → Ê.
        assert transliterate_iso9("Євген") == "Êvgen"
        # І → Ì.
        assert transliterate_iso9("Іван") == "Ìvan"

    def test_belarusian_letter_u_short(self) -> None:
        # Ў → Ŭ.
        assert transliterate_iso9("Воўк") == "Voŭk"


class TestHistoricalCyrillic:
    def test_yat(self) -> None:
        # Ѣ → Ě (используется в дорев. метриках).
        assert transliterate_iso9("Сѣдой") == "Sědoj"

    def test_fita_lowercase(self) -> None:
        # ѳ → f̀ (фита, F + combining grave).
        out = transliterate_iso9("ѳеодоръ")
        assert out.startswith("f")  # F + grave
        assert "ʺ" in out  # Ъ в конце


# -----------------------------------------------------------------------------
# Свойства: регистр, идентичность
# -----------------------------------------------------------------------------


class TestProperties:
    def test_case_preserved_within_word(self) -> None:
        assert transliterate_iso9("Жжж") == "Žžž"

    def test_pure_latin_unchanged(self) -> None:
        assert transliterate_iso9("Hello, World!") == "Hello, World!"

    def test_mixed_text(self) -> None:
        # Латиница + кириллица — латиница нетронута.
        assert transliterate_iso9("Ivan (Иван)") == "Ivan (Ivan)"


# -----------------------------------------------------------------------------
# is_cyrillic
# -----------------------------------------------------------------------------


class TestIsCyrillic:
    @pytest.mark.parametrize(
        "text",
        ["Иван", "Б", "Слоним, Россия", "Test Иван", "Київ", "ѣ"],
    )
    def test_returns_true_for_cyrillic(self, text: str) -> None:
        assert is_cyrillic(text) is True

    @pytest.mark.parametrize(
        "text",
        ["", "Hello", "1234", "John Smith", "...", "100%"],
    )
    def test_returns_false_for_non_cyrillic(self, text: str) -> None:
        assert is_cyrillic(text) is False
