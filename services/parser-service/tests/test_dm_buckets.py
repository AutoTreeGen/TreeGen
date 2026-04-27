"""Unit-тесты для DM-bucket helper'ов (Phase 4.4.1)."""

from __future__ import annotations

from parser_service.services.dm_buckets import (
    compute_dm_buckets,
    merge_dm_buckets,
    transliterate_cyrillic,
)


class TestTransliterate:
    def test_pure_latin_passes_through(self) -> None:
        assert transliterate_cyrillic("Smith") == "Smith"

    def test_empty_returns_empty(self) -> None:
        assert transliterate_cyrillic("") == ""

    def test_cyrillic_uppercase(self) -> None:
        assert transliterate_cyrillic("ЖИТНИЦКИЙ") == "ZHITNITSKIY"

    def test_cyrillic_titlecase_preserves_case(self) -> None:
        # «Житницкий» — Ж заглавная → digraph "ZH" пишется обоими-uppercase
        # (ZHitnitskiy). Стилистически выглядит странно для отображения, но
        # для DM-матчинга безразлично (DM нормализует всё в uppercase).
        assert transliterate_cyrillic("Житницкий") == "ZHitnitskiy"

    def test_digits_and_punct_pass_through(self) -> None:
        assert transliterate_cyrillic("Иван-2") == "Ivan-2"

    def test_softsign_stripped(self) -> None:
        assert transliterate_cyrillic("Соль") == "Sol"

    def test_mixed_latin_cyrillic(self) -> None:
        # Имена в реальных GED иногда имеют латинскую часть имени и
        # кириллическую фамилию — обе должны корректно проходить.
        assert transliterate_cyrillic("John Иванов") == "John Ivanov"


class TestComputeDmBuckets:
    def test_empty_returns_empty(self) -> None:
        assert compute_dm_buckets("") == []
        assert compute_dm_buckets(None) == []

    def test_single_latin_name(self) -> None:
        codes = compute_dm_buckets("Smith")
        assert codes
        assert all(len(c) == 6 and c.isdigit() for c in codes)

    def test_cyrillic_zhitnitsky_matches_latin_zhitnitzky(self) -> None:
        """Главный сигнал проекта: ``Zhitnitzky`` ↔ ``Житницкий`` — один bucket-set."""
        latin = set(compute_dm_buckets("Zhitnitzky"))
        cyrillic = set(compute_dm_buckets("Житницкий"))
        assert latin
        assert cyrillic
        # Буквальное совпадение хотя бы одного кода — достаточно для
        # phonetic match через operator `&&` (arrays overlap).
        assert latin & cyrillic, f"no overlap: latin={latin}, cyrillic={cyrillic}"

    def test_cohen_variants_match(self) -> None:
        """Cohen / Kohen / Cohn — классический DM ambivalence-кейс."""
        cohen = set(compute_dm_buckets("Cohen"))
        kohen = set(compute_dm_buckets("Kohen"))
        cohn = set(compute_dm_buckets("Cohn"))
        assert cohen & kohen
        assert cohen & cohn

    def test_purely_non_alphabetic_returns_empty(self) -> None:
        # DM-нормализация стрипает всё кроме A-Z; пунктуация → no codes.
        assert compute_dm_buckets("---") == []
        assert compute_dm_buckets("123") == []


class TestMergeDmBuckets:
    def test_empty_iter_returns_empty(self) -> None:
        assert merge_dm_buckets([]) == []

    def test_none_and_empty_filtered(self) -> None:
        assert merge_dm_buckets([None, "", None]) == []

    def test_single_name(self) -> None:
        merged = merge_dm_buckets(["Smith"])
        assert merged == sorted(set(compute_dm_buckets("Smith")))

    def test_union_of_multiple_names_dedup(self) -> None:
        # BIRTH + AKA — обе вариации одного человека, ожидаем union.
        merged = merge_dm_buckets(["Zhitnitzky", "Житницкий"])
        latin = set(compute_dm_buckets("Zhitnitzky"))
        cyr = set(compute_dm_buckets("Житницкий"))
        assert set(merged) == latin | cyr
        # Sorted для детерминизма.
        assert merged == sorted(merged)

    def test_no_duplicates_in_output(self) -> None:
        # Передаём одну и ту же фамилию два раза — output должен быть unique.
        merged = merge_dm_buckets(["Smith", "Smith", "Smith"])
        assert len(merged) == len(set(merged))
