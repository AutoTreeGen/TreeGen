"""Tests for :func:`entity_resolution.names.generate_archive_variants` (Phase 15.10)."""

from __future__ import annotations

from entity_resolution.names import generate_archive_variants
from entity_resolution.names.variants import _DM_PREFIX


def _spelling_only(variants: set[str]) -> set[str]:
    """Variants без DM-codes."""
    return {v for v in variants if not v.startswith(_DM_PREFIX)}


def _dm_codes(variants: set[str]) -> set[str]:
    return {v[len(_DM_PREFIX) :] for v in variants if v.startswith(_DM_PREFIX)}


class TestLatinInput:
    def test_levitin_includes_cyrillic_form(self) -> None:
        variants = generate_archive_variants("Levitin")
        spellings = _spelling_only(variants)
        # Cyrillic form через synonym table или Latin → Cyrillic transliteration.
        assert any(any(0x0400 <= ord(c) <= 0x04FF for c in v) for v in spellings), (
            f"Expected Cyrillic variant for Levitin, got: {spellings}"
        )

    def test_includes_dm_codes(self) -> None:
        variants = generate_archive_variants("Levitin")
        codes = _dm_codes(variants)
        assert codes, f"Expected DM codes, got: {variants}"

    def test_includes_self(self) -> None:
        variants = generate_archive_variants("Levitin")
        assert "Levitin" in variants

    def test_jewish_surname_includes_hebrew(self) -> None:
        # «Friedman» — AJ surname (suffix -man), expect Hebrew variant.
        variants = generate_archive_variants("Friedman")
        spellings = _spelling_only(variants)
        # Synonyms file contains Hebrew form for Friedman.
        has_hebrew = any(any(0x0590 <= ord(c) <= 0x05FF for c in v) for v in spellings)
        assert has_hebrew, f"Expected Hebrew variant for Friedman, got: {spellings}"


class TestCyrillicInput:
    def test_levitin_cyrillic_includes_latin(self) -> None:
        variants = generate_archive_variants("Левитин")
        spellings = _spelling_only(variants)
        assert "Levitin" in spellings or any("evitin" in v.lower() for v in spellings), (
            f"Expected Latin Levitin, got: {spellings}"
        )

    def test_cyrillic_self_preserved(self) -> None:
        variants = generate_archive_variants("Левитин")
        assert "Левитин" in variants


class TestPolishDiacritic:
    def test_lukasz_includes_both_forms(self) -> None:
        variants = generate_archive_variants("Łukasz")
        spellings = _spelling_only(variants)
        assert "Łukasz" in spellings
        assert "Lukasz" in spellings


class TestGermanDiacritic:
    def test_muller_round_trip(self) -> None:
        variants = generate_archive_variants("Müller")
        spellings = _spelling_only(variants)
        assert "Müller" in spellings
        assert "Mueller" in spellings


class TestEdgeCases:
    def test_empty_input(self) -> None:
        # Empty preserves itself; не raises'ит.
        variants = generate_archive_variants("")
        assert variants == {""}

    def test_whitespace_only(self) -> None:
        variants = generate_archive_variants("   ")
        assert variants == {"   "}

    def test_explicit_source_lang_pl(self) -> None:
        variants = generate_archive_variants("Kowalski", source_lang="pl")
        spellings = _spelling_only(variants)
        assert "Kowalski" in spellings
        # Должен быть Cyrillic вариант через transliterate fallback'ом
        # (Polish input → лат. → lat→cyr).
        assert any(any(0x0400 <= ord(c) <= 0x04FF for c in v) for v in spellings)

    def test_minimum_set_size(self) -> None:
        # Любой непустой ввод → ≥ 1 spelling + ≥ 1 DM код в обычном случае.
        variants = generate_archive_variants("Smith")
        assert len(variants) >= 2  # self + at least one DM/diacritic candidate
