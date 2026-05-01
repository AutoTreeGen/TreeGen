"""Tests for :class:`entity_resolution.names.Transliterator` (Phase 15.10)."""

from __future__ import annotations

import pytest
from entity_resolution.names import Transliterator


class TestCyrillicToLatin:
    @pytest.fixture
    def tl(self) -> Transliterator:
        return Transliterator()

    def test_levitin_bgn(self, tl: Transliterator) -> None:
        assert tl.to_latin("Левитин", source_script="cyrillic", standard="bgn") == "Levitin"

    def test_levitin_loc(self, tl: Transliterator) -> None:
        assert tl.to_latin("Левитин", source_script="cyrillic", standard="loc") == "Levitin"

    def test_petrov_bgn(self, tl: Transliterator) -> None:
        assert tl.to_latin("Петров", source_script="cyrillic", standard="bgn") == "Petrov"

    def test_zhitnitzky_bgn_uses_zh(self, tl: Transliterator) -> None:
        # ж → zh в BGN
        result = tl.to_latin("Житницкий", source_script="cyrillic", standard="bgn")
        assert result.startswith(("Zhitnit", "Zh"))

    def test_iso9_uses_diacritics(self, tl: Transliterator) -> None:
        # ISO 9 для ж → ž (с háček'ом).
        result = tl.to_latin("Шапиро", source_script="cyrillic", standard="iso9")
        assert "š" in result.lower()

    def test_empty_input(self, tl: Transliterator) -> None:
        assert tl.to_latin("", source_script="cyrillic", standard="bgn") == ""

    def test_unknown_chars_passthrough(self, tl: Transliterator) -> None:
        assert tl.to_latin("123", source_script="cyrillic", standard="bgn") == "123"


class TestLatinToCyrillic:
    @pytest.fixture
    def tl(self) -> Transliterator:
        return Transliterator()

    def test_levitin_returns_cyrillic(self, tl: Transliterator) -> None:
        result = tl.to_cyrillic("Levitin", source_script="latin")
        assert any(0x0400 <= ord(ch) <= 0x04FF for ch in result), f"expected Cyrillic in {result!r}"

    def test_empty_input(self, tl: Transliterator) -> None:
        assert tl.to_cyrillic("", source_script="latin") == ""


class TestHebrewToLatin:
    @pytest.fixture
    def tl(self) -> Transliterator:
        return Transliterator()

    def test_lvyttn_bgn_yields_latin(self, tl: Transliterator) -> None:
        # «לויטין» Hebrew transliteration via BGN.
        result = tl.to_latin("לויטין", source_script="hebrew", standard="bgn")
        # BGN: ל=l, ו=v, י=y, ט=t, ן=נ→n. Should not be empty.
        assert result
        assert all(ord(ch) < 256 or ch in "ʼʻ" for ch in result)

    def test_final_letter_normalisation(self, tl: Transliterator) -> None:
        # «ן» (final nun) должна нормализоваться к «נ» → n.
        result = tl.to_latin("ן", source_script="hebrew", standard="bgn")
        assert result.lower() == "n"


class TestNormalizeDiacritics:
    @pytest.fixture
    def tl(self) -> Transliterator:
        return Transliterator()

    def test_german_umlaut_round_trip(self, tl: Transliterator) -> None:
        out = tl.normalize_diacritics("Müller", lang="de")
        assert "Müller" in out
        assert "Mueller" in out

    def test_german_ae_form_to_umlaut(self, tl: Transliterator) -> None:
        out = tl.normalize_diacritics("Mueller", lang="de")
        assert "Müller" in out
        assert "Mueller" in out

    def test_polish_lukasz(self, tl: Transliterator) -> None:
        out = tl.normalize_diacritics("Łukasz", lang="pl")
        assert "Lukasz" in out
        assert "Łukasz" in out

    def test_czech_hacek(self, tl: Transliterator) -> None:
        out = tl.normalize_diacritics("Černý", lang="cs")
        assert "Černý" in out
        assert "Cerny" in out

    def test_cyrillic_input_no_unidecode_pollution(self, tl: Transliterator) -> None:
        """Левитин → diacritic-fold НЕ должен делать ASCII-form через unidecode.

        Иначе `_diacritic_variants` для Левитина включит `Levitin` и
        diacritic-стадия NameMatcher'а ошибочно сматчит cross-script —
        это должно быть `variant_transliteration`, не `variant_diacritic`.
        """
        out = tl.normalize_diacritics("Левитин", lang="pl")
        assert "Levitin" not in out
        assert "Левитин" in out

    def test_empty_input(self, tl: Transliterator) -> None:
        out = tl.normalize_diacritics("", lang="pl")
        assert out == {""}
