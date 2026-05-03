"""Tests for ai_layer.multilingual (Phase 10.9e slice A).

Покрывает:

* Cyrillic Russian → BGN/PCGN Latin.
* Hebrew → ALA-LC Latin.
* English / Latin passthrough — identity, regression-stable for Geoffrey-demo.
* Auto-detect (locale=None) by Unicode-block heuristic.
* Empty input.
* Locale → script mapping (uk/be/bg/sr → cyrillic, yi → hebrew).
* Unknown locale falls back to text auto-detect.
"""

from __future__ import annotations

import pytest
from ai_layer.multilingual import TransliteratedName, transliterate_for_locale


def test_russian_explicit_locale_to_bgn_latin() -> None:
    result = transliterate_for_locale("Иван Петрович", locale="ru")
    assert result.original == "Иван Петрович"
    assert result.script == "cyrillic"
    # BGN/PCGN: И→I, в→v, а→a, н→n, П→P, е→e, т→t, р→r, о→o, ч→ch, и→i
    assert result.latin == "Ivan Petrovich"


def test_hebrew_explicit_locale_to_loc_latin() -> None:
    result = transliterate_for_locale("מאיר", locale="he")
    assert result.original == "מאיר"
    assert result.script == "hebrew"
    # LoC Hebrew: מ→m, א→ʼ, י→y, ר→r — exact form depends on 15.10 table.
    # Assert non-empty and Latin-only (no Hebrew chars survived).
    assert result.latin
    assert all(ord(c) < 0x0590 or ord(c) > 0x05FF for c in result.latin)


def test_english_passthrough_is_identity() -> None:
    """Anti-regression: EN input round-trips byte-for-byte."""
    result = transliterate_for_locale("John Doe", locale="en")
    assert result.original == "John Doe"
    assert result.latin == "John Doe"
    assert result.script == "latin"


def test_already_latin_no_locale_passthrough() -> None:
    """No locale + Latin chars → identity, script=latin (Geoffrey-demo path)."""
    result = transliterate_for_locale("Geoffrey Michael")
    assert result.original == "Geoffrey Michael"
    assert result.latin == "Geoffrey Michael"
    assert result.script == "latin"


def test_auto_detect_cyrillic_no_locale() -> None:
    """No locale + Cyrillic chars → auto-detected as cyrillic, transliterated."""
    result = transliterate_for_locale("Лев Толстой")
    assert result.script == "cyrillic"
    assert result.latin
    assert result.latin != result.original


def test_auto_detect_hebrew_no_locale() -> None:
    result = transliterate_for_locale("שרה")
    assert result.script == "hebrew"
    assert result.latin
    assert result.latin != result.original


def test_empty_input() -> None:
    result = transliterate_for_locale("", locale="ru")
    assert result == TransliteratedName(original="", latin="", script="latin")


@pytest.mark.parametrize("locale", ["uk", "be", "bg", "sr"])
def test_other_cyrillic_locales_use_cyrillic_bucket(locale: str) -> None:
    """Ukrainian / Belarusian / Bulgarian / Serbian — share Cyrillic transliteration."""
    result = transliterate_for_locale("Іван", locale=locale)
    assert result.script == "cyrillic"


def test_yiddish_locale_uses_hebrew_bucket() -> None:
    """Yiddish locale routes to Hebrew script per 15.10's lexicon-collapse note."""
    result = transliterate_for_locale("חיים", locale="yi")
    assert result.script == "hebrew"


def test_auto_locale_string_treated_as_no_hint() -> None:
    """``locale='auto'`` triggers script-detection fallback."""
    result = transliterate_for_locale("Иван", locale="auto")
    assert result.script == "cyrillic"
    assert result.latin
    assert result.latin != "Иван"


def test_unknown_locale_falls_back_to_text_detection() -> None:
    """Locale not in mapping (e.g. 'xx') → infer from text."""
    result = transliterate_for_locale("Иван", locale="xx")
    assert result.script == "cyrillic"


def test_mixed_script_uses_first_script_bearing_char() -> None:
    """First non-Latin character wins; remainder transliterated by that bucket."""
    result = transliterate_for_locale("Иван 王", locale=None)
    assert result.script == "cyrillic"
