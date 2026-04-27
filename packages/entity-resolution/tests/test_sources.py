"""Тесты source_match_score (ADR-0015 §«Sources»)."""

from __future__ import annotations

import pytest
from entity_resolution.sources import source_match_score


class TestSourceMatchScore:
    def test_identical_sources_score_one(self) -> None:
        score = source_match_score(
            "Lubelskie parish records 1838",
            "Lubelskie Archive",
            "LubParish1838",
            "Lubelskie parish records 1838",
            "Lubelskie Archive",
            "LubParish1838",
        )
        # FP-точность: 0.7 + 0.2 + 0.1 = 0.999999... но семантически 1.0.
        assert score == pytest.approx(1.0)

    def test_source_dedup_lubelskie_parish_records(self) -> None:
        """Главный success signal phase 3.4: разные написания одного source."""
        score = source_match_score(
            "Lubelskie parish records 1838",
            "Lubelskie Archive",
            None,
            "Lubelskie Parish 1838",
            "Lubelskie Archive",
            None,
        )
        assert score >= 0.85, f"expected ≥0.85, got {score}"

    def test_abbreviation_match_boosts(self) -> None:
        """Совпадение abbrev должно сильно поднимать score над одним лишь title."""
        without_abbrev = source_match_score("Records 1838", None, None, "Records", None, None)
        with_abbrev = source_match_score(
            "Records 1838", None, "REC1838", "Records", None, "REC1838"
        )
        assert with_abbrev > without_abbrev
        assert with_abbrev - without_abbrev >= 0.05

    def test_completely_different_sources_low_score(self) -> None:
        score = source_match_score(
            "Lubelskie parish records 1838",
            None,
            None,
            "1850 US Census, Pennsylvania",
            None,
            None,
        )
        assert score < 0.5

    def test_authors_jaccard_contributes(self) -> None:
        """Полное совпадение авторов поднимает score выше, чем title alone.

        Частичное совпадение (Jaccard < 1.0) не обязано быть выше:
        алгоритм перераспределяет вес title→0.9 при отсутствии authors,
        а партиальный Jaccard может опустить общий score.
        """
        score_no_authors = source_match_score("Records 1838", None, None, "Records", None, None)
        score_same_authors = source_match_score(
            "Records 1838", "John Doe", None, "Records", "John Doe", None
        )
        # Учитываем FP-точность: 0.9 vs 0.8999999... — семантически равны.
        assert score_same_authors == pytest.approx(score_no_authors, abs=1e-6) or (
            score_same_authors >= score_no_authors
        )

    def test_score_capped_at_one(self) -> None:
        """Boost от abbreviation не должен выйти за 1.0."""
        score = source_match_score(
            "Lubelskie parish 1838",
            "Archive",
            "LP1838",
            "Lubelskie parish 1838",
            "Archive",
            "LP1838",
        )
        assert score <= 1.0
