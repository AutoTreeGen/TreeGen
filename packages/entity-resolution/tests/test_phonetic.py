"""Тесты Soundex / Daitch-Mokotoff (ADR-0015 §«Daitch-Mokotoff»)."""

from __future__ import annotations

from entity_resolution.phonetic import daitch_mokotoff, soundex


class TestSoundex:
    def test_empty_string_returns_empty(self) -> None:
        assert soundex("") == ""
        assert soundex("   ") == ""

    def test_returns_string(self) -> None:
        result = soundex("Smith")
        assert isinstance(result, str)
        assert result != ""

    def test_zhitnitzky_variants_share_soundex_or_dm(self) -> None:
        """Минимум одна из систем кодеров должна свести варианты к одному коду.

        Soundex иногда не справляется со славянскими корнями (Zh / Zh — один
        старт-символ), поэтому accept'ится «или Soundex или DM пересеклись».
        Конкретное assertion на DM — в test_dm_handles_slavic_to_english_transliteration.
        """
        a = soundex("Zhitnitzky")
        b = soundex("Zhitnitsky")
        # Минор различия в спеллинге → одинаковый Soundex по идее.
        assert a == b


class TestDaitchMokotoff:
    def test_empty_string_returns_empty_list(self) -> None:
        assert daitch_mokotoff("") == []
        assert daitch_mokotoff("   ") == []

    def test_returns_non_empty_for_simple_name(self) -> None:
        codes = daitch_mokotoff("Smith")
        assert isinstance(codes, list)
        assert codes
        assert all(isinstance(code, str) for code in codes)

    def test_dm_handles_slavic_to_english_transliteration(self) -> None:
        """Транслитерации одной фамилии должны давать пересекающиеся DM-коды.

        Это ключевой success signal phase 3.4 — без него многократный
        импорт от Ancestry / MyHeritage / Geni не дедупится.
        """
        zhitnitzky = set(daitch_mokotoff("Zhitnitzky"))
        zhitnitsky = set(daitch_mokotoff("Zhitnitsky"))
        zhytnicki = set(daitch_mokotoff("Zhytnicki"))
        assert zhitnitzky, "Zhitnitzky should produce DM codes"
        assert zhitnitsky, "Zhitnitsky should produce DM codes"
        assert zhytnicki, "Zhytnicki should produce DM codes"
        # Все три варианта должны попасть в одно множество DM-кодов
        # (хотя бы один общий код у каждой пары).
        assert zhitnitzky & zhitnitsky, f"Zhitnitzky {zhitnitzky} ∩ Zhitnitsky {zhitnitsky} == ∅"
        assert zhitnitzky & zhytnicki, f"Zhitnitzky {zhitnitzky} ∩ Zhytnicki {zhytnicki} == ∅"

    def test_distinct_surnames_have_disjoint_codes(self) -> None:
        """Разнородные фамилии не должны жить в одном bucket'е."""
        smith = set(daitch_mokotoff("Smith"))
        zhitnitzky = set(daitch_mokotoff("Zhitnitzky"))
        assert smith
        assert zhitnitzky
        assert not (smith & zhitnitzky), "Smith and Zhitnitzky must not share DM codes"
