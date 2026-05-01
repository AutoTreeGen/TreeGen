"""Tests for :class:`entity_resolution.names.NameMatcher` (Phase 15.10).

Includes:

* Reason-attribution для каждой стадии (exact / diacritic / synonym /
  transliteration / DM / fuzzy).
* AJ / Slavic dogfood — anchor surnames из ``icp_anchor_synonyms.json``
  (Levitin / Cohen / Katz / Friedman / Baron) plus Polish/German diacritic
  + Slavic patronymic-aware names.
* Backward-compat: все три флага False → fuzzy + exact only.
"""

from __future__ import annotations

import pytest
from entity_resolution.names import NameMatcher


@pytest.fixture
def matcher() -> NameMatcher:
    return NameMatcher()


class TestExact:
    def test_exact_case_insensitive(self, matcher: NameMatcher) -> None:
        results = matcher.match("Levitin", ["levitin"], min_score=0.0)
        assert results
        assert results[0].reason == "exact"
        assert results[0].score == pytest.approx(1.0)


class TestDiacritic:
    def test_polish_lukasz(self, matcher: NameMatcher) -> None:
        results = matcher.match("Łukasz", ["Lukasz"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_diacritic"

    def test_german_muller(self, matcher: NameMatcher) -> None:
        results = matcher.match("Müller", ["Mueller"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_diacritic"

    def test_german_reverse(self, matcher: NameMatcher) -> None:
        results = matcher.match("Mueller", ["Müller"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_diacritic"


class TestSynonym:
    def test_levitin_anchor(self, matcher: NameMatcher) -> None:
        results = matcher.match("Levitin", ["Левитин"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_synonym"

    def test_friedman_to_frydman(self, matcher: NameMatcher) -> None:
        results = matcher.match("Friedman", ["Frydman"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_synonym"

    def test_katz_to_cyrillic(self, matcher: NameMatcher) -> None:
        results = matcher.match("Katz", ["Кац"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_synonym"

    def test_cohen_to_kogan_synonym(self, matcher: NameMatcher) -> None:
        results = matcher.match("Cohen", ["Каган"], min_score=0.0)
        assert results
        assert results[0].reason == "variant_synonym"


class TestTransliteration:
    """Cross-script с именами, не входящими в anchor-table."""

    def test_zhukov_cross_script(self, matcher: NameMatcher) -> None:
        # Zhukov НЕ в anchor-table → транслитерация через canonical-fold.
        results = matcher.match("Zhukov", ["Жуков"], min_score=0.0)
        assert results
        # `Zhukov` через unidecode = `Zhukov`; `Жуков` через unidecode = `Zhukov`.
        # → canonical_form intersect → variant_transliteration.
        assert results[0].reason == "variant_transliteration"


class TestDmPhonetic:
    def test_distinct_spellings_same_phonetic(self, matcher: NameMatcher) -> None:
        # Zhitnitzky/Zhytnicki — известный DM-anchor case (см. ADR-0015).
        results = matcher.match("Zhitnitzky", ["Zhytnicki"], min_score=0.0)
        assert results
        # Может быть transliteration (canonical fold equal'ит) или dm_phonetic.
        # Главное — не "fuzzy".
        assert results[0].reason in ("variant_transliteration", "dm_phonetic", "exact")

    def test_via_contains_dm_code_when_phonetic(self) -> None:
        # Disable variants/synonyms так чтобы DM-стадия точно сработала.
        m = NameMatcher(use_variants=False, use_synonyms=False, use_phonetic=True)
        results = m.match("Schwartz", ["Shvartz"], min_score=0.0)
        assert results
        if results[0].reason == "dm_phonetic":
            assert results[0].via is not None
            assert "dm_code" in results[0].via


class TestFuzzyAndNoMatch:
    def test_no_match_below_threshold(self, matcher: NameMatcher) -> None:
        results = matcher.match("Levitin", ["Smith"], min_score=0.7)
        # Levitin / Smith — ни один shared-reason, fuzzy-ratio низкий.
        assert results == []

    def test_min_score_filter(self, matcher: NameMatcher) -> None:
        results = matcher.match("Levitin", ["XXXX"], min_score=0.7)
        assert results == []


class TestBackwardCompat:
    def test_all_flags_off_returns_fuzzy_or_exact_only(self) -> None:
        m = NameMatcher(use_variants=False, use_phonetic=False, use_synonyms=False)
        # Levitin/Левитин в этом режиме НЕ должен матчиться (fuzzy
        # на разных script'ах слишком низкий, и synonyms выключены).
        results = m.match("Levitin", ["Левитин"], min_score=0.7)
        assert results == []

    def test_all_flags_off_exact_works(self) -> None:
        m = NameMatcher(use_variants=False, use_phonetic=False, use_synonyms=False)
        results = m.match("Levitin", ["levitin"], min_score=0.0)
        assert results
        assert results[0].reason == "exact"


class TestRanking:
    def test_results_sorted_by_score_desc(self, matcher: NameMatcher) -> None:
        results = matcher.match(
            "Levitin",
            ["Levitin", "Левитин", "XXXX"],
            min_score=0.0,
        )
        # Levitin (exact) > Левитин (synonym/transliteration).
        assert len(results) >= 2
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)


class TestDogfoodAJSlavic:
    """ICP-anchor lineages из owner's domain (AJ-endogamous, see project memory).

    Эти cases — **regression guard** для Phase 15.10 с т.з. ICP user'а;
    если ровно эти кейсы перестают работать — кто-то поломал anchor table
    или матчер.
    """

    @pytest.mark.parametrize(
        ("query", "candidate"),
        [
            ("Levitin", "Левитин"),
            ("Левитин", "Levitin"),
            ("Cohen", "Kogan"),
            ("Cohen", "Каган"),
            ("Katz", "Kac"),
            ("Katz", "Кац"),
            ("Friedman", "Frydman"),
            ("Friedman", "Фридман"),
            ("Baron", "Барон"),
            ("Rabinowitz", "Rabinovich"),
            ("Davidov", "Давыдов"),
        ],
    )
    def test_anchor_pair_matches(self, query: str, candidate: str) -> None:
        m = NameMatcher()
        results = m.match(query, [candidate], min_score=0.7)
        assert results, f"Expected match for ({query!r}, {candidate!r})"
        # Любой не-fuzzy reason — ICP-anchor должны всегда давать
        # explainable match.
        assert results[0].reason != "fuzzy"
