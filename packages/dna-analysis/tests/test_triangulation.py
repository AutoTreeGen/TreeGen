"""Тесты triangulation engine (Phase 6.4 / ADR-0054).

Все тесты используют синтетические Match-объекты. Никаких реальных
DNA-данных, никаких bp-координат — только cM (см. ADR-0014 §«Privacy
guards в коде»).
"""

from __future__ import annotations

import logging

import pytest
from dna_analysis import (
    Match,
    TriangulationGroup,
    TriangulationSegment,
    bayes_boost,
    find_triangulation_groups,
)
from dna_analysis.triangulation import (
    DEFAULT_MIN_OVERLAP_CM,
    ENDOGAMY_MEMBER_COUNT_THRESHOLD,
)


def _seg(chromosome: int, start_cm: float, end_cm: float) -> TriangulationSegment:
    """Сокращённый конструктор сегмента для читабельности тестов."""
    return TriangulationSegment(chromosome=chromosome, start_cm=start_cm, end_cm=end_cm)


def _make_match(
    match_id: str,
    *,
    segments: tuple[TriangulationSegment, ...] = (),
    shared_match_ids: frozenset[str] = frozenset(),
    has_known_mrca: bool = False,
) -> Match:
    """Compact factory для Match-объектов в тестах."""
    return Match(
        match_id=match_id,
        segments=segments,
        shared_match_ids=shared_match_ids,
        has_known_mrca=has_known_mrca,
    )


# --- find_triangulation_groups -----------------------------------------------


class TestFindTriangulationGroups:
    """Базовые сценарии поведения движка."""

    def test_empty_input_returns_empty_list(self) -> None:
        assert find_triangulation_groups([]) == []

    def test_two_matches_with_no_shared_relation_no_groups(self) -> None:
        """Две matches на одном сегменте, но НЕ shared matches → нет триангуляции.

        Триангуляция требует mutual shared_match relation: сам факт
        пересечения сегментов недостаточен (это могла бы быть случайность).
        """
        m_a = _make_match("a", segments=(_seg(1, 10.0, 25.0),))
        m_b = _make_match("b", segments=(_seg(1, 10.0, 25.0),))
        assert find_triangulation_groups([m_a, m_b]) == []

    def test_three_matches_with_overlap_and_mutual_relation(self) -> None:
        """A, B, C попарно shared matches, IBD-сегменты пересекаются на
        chr1 от 12 до 24 cM → одна группа из 3 members."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 30.0),),
            shared_match_ids=frozenset({"b", "c"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 12.0, 28.0),),
            shared_match_ids=frozenset({"a", "c"}),
        )
        m_c = _make_match(
            "c",
            segments=(_seg(1, 8.0, 24.0),),
            shared_match_ids=frozenset({"a", "b"}),
        )

        groups = find_triangulation_groups([m_a, m_b, m_c])

        assert len(groups) == 1
        group = groups[0]
        assert group.chromosome == 1
        assert group.members == ("a", "b", "c")
        # Финальный интервал — пересечение всех трёх сегментов = [12, 24].
        assert group.start_cm == 12.0
        assert group.end_cm == 24.0
        assert group.confidence_boost == 1.0  # bayes_boost вызывается отдельно

    def test_overlap_exactly_at_threshold_is_included(self) -> None:
        """Edge case: overlap ровно ``min_overlap_cm`` cM — входит (≥, не >)."""
        threshold = DEFAULT_MIN_OVERLAP_CM
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 10.0 + threshold),),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 10.0, 10.0 + threshold),),
            shared_match_ids=frozenset({"a"}),
        )

        groups = find_triangulation_groups([m_a, m_b], min_overlap_cm=threshold)

        assert len(groups) == 1
        assert groups[0].end_cm - groups[0].start_cm == pytest.approx(threshold)

    def test_overlap_just_below_threshold_is_excluded(self) -> None:
        """Edge case: overlap (threshold - 0.01) cM — отброшен."""
        threshold = DEFAULT_MIN_OVERLAP_CM
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 10.0 + threshold - 0.01),),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 10.0, 10.0 + threshold - 0.01),),
            shared_match_ids=frozenset({"a"}),
        )
        assert find_triangulation_groups([m_a, m_b], min_overlap_cm=threshold) == []

    def test_segments_on_different_chromosomes_do_not_triangulate(self) -> None:
        """Один и тот же overlap-диапазон, но на разных хромосомах → нет группы."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 25.0),),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(7, 10.0, 25.0),),
            shared_match_ids=frozenset({"a"}),
        )
        assert find_triangulation_groups([m_a, m_b]) == []

    def test_one_way_shared_match_relation_is_ignored(self) -> None:
        """A→B shared, но B→A не shared — mutual relation не выполнена,
        триплет не создаётся (асимметричные exports от платформ —
        ошибка ETL, fail-closed)."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 30.0),),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 12.0, 28.0),),
            shared_match_ids=frozenset(),  # B не знает про A
        )
        assert find_triangulation_groups([m_a, m_b]) == []

    def test_multiple_chromosomes_yield_separate_groups(self) -> None:
        """Один и тот же набор matches триангулирует на двух хромосомах
        независимо → две группы, отсортированные по chromosome."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 30.0), _seg(5, 50.0, 70.0)),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 12.0, 28.0), _seg(5, 52.0, 68.0)),
            shared_match_ids=frozenset({"a"}),
        )

        groups = find_triangulation_groups([m_a, m_b])

        assert [g.chromosome for g in groups] == [1, 5]
        for group in groups:
            assert group.members == ("a", "b")

    def test_endogamy_synthetic_case_groups_all_members(self) -> None:
        """11 matches на одном сегменте, попарно shared → одна большая группа.

        Дальше caller вызовет :func:`bayes_boost` который вернёт 0.5x —
        флаг «вероятно endogamy» (см. отдельный тест ниже).
        """
        match_ids = [f"m{i:02d}" for i in range(11)]
        ids_set = frozenset(match_ids)
        matches = [
            _make_match(
                mid,
                segments=(_seg(1, 10.0, 30.0),),
                shared_match_ids=ids_set - {mid},
            )
            for mid in match_ids
        ]

        groups = find_triangulation_groups(matches)

        assert len(groups) == 1
        group = groups[0]
        assert len(group.members) == 11
        assert group.chromosome == 1
        assert group.start_cm == 10.0
        assert group.end_cm == 30.0

    def test_two_independent_pairs_on_same_chromosome_yield_two_groups(self) -> None:
        """Пара (A, B) на 10–25 cM и пара (C, D) на 60–80 cM, нет
        cross-shared — две независимые группы."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 25.0),),
            shared_match_ids=frozenset({"b"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 10.0, 25.0),),
            shared_match_ids=frozenset({"a"}),
        )
        m_c = _make_match(
            "c",
            segments=(_seg(1, 60.0, 80.0),),
            shared_match_ids=frozenset({"d"}),
        )
        m_d = _make_match(
            "d",
            segments=(_seg(1, 60.0, 80.0),),
            shared_match_ids=frozenset({"c"}),
        )

        groups = find_triangulation_groups([m_a, m_b, m_c, m_d])

        assert len(groups) == 2
        assert [g.members for g in groups] == [("a", "b"), ("c", "d")]

    def test_disjoint_geometry_through_shared_member_yields_separate_groups(
        self,
    ) -> None:
        """A связан с B по [10, 25], B связан с C по [60, 75] на той же
        хромосоме. Все попарно shared matches, но triplets имеют общего
        члена B при непересекающейся геометрии.

        Контракт алгоритма (ADR-0054 §«Решение»): два triplet'а
        объединяются только если **И** делят member, **И** их интервалы
        пересекаются ≥ ``min_overlap_cm``. Здесь второе условие нарушено
        (∅ ∩ [10,25] [60,75]), поэтому остаются две независимые группы:
        кит-owner действительно триангулирует с B на двух разных
        участках хромосомы (разные сегменты IBD от B), и эти случаи
        не должны схлопываться в один."""
        m_a = _make_match(
            "a",
            segments=(_seg(1, 10.0, 25.0),),
            shared_match_ids=frozenset({"b", "c"}),
        )
        m_b = _make_match(
            "b",
            segments=(_seg(1, 10.0, 25.0), _seg(1, 60.0, 75.0)),
            shared_match_ids=frozenset({"a", "c"}),
        )
        m_c = _make_match(
            "c",
            segments=(_seg(1, 60.0, 75.0),),
            shared_match_ids=frozenset({"a", "b"}),
        )

        groups = find_triangulation_groups([m_a, m_b, m_c])

        assert len(groups) == 2
        # Sorted by (chromosome, start_cm) — сначала [10, 25], потом [60, 75].
        assert groups[0].members == ("a", "b")
        assert (groups[0].start_cm, groups[0].end_cm) == (10.0, 25.0)
        assert groups[1].members == ("b", "c")
        assert (groups[1].start_cm, groups[1].end_cm) == (60.0, 75.0)

    def test_empty_segments_match_does_not_break(self) -> None:
        """Match без сегментов — допустим, в input не ломает."""
        m_a = _make_match("a", shared_match_ids=frozenset({"b"}))
        m_b = _make_match("b", shared_match_ids=frozenset({"a"}))
        assert find_triangulation_groups([m_a, m_b]) == []

    def test_invalid_min_overlap_raises(self) -> None:
        """Защита от пагубных вызовов с 0 / отрицательным порогом."""
        with pytest.raises(ValueError, match="min_overlap_cm must be positive"):
            find_triangulation_groups([], min_overlap_cm=0.0)

    def test_logs_only_aggregates_no_match_ids_or_segments(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Privacy-инвариант: в logs только агрегированная статистика, без
        match_id, без cM-координат отдельных segments."""
        m_a = _make_match(
            "secret-match-a",
            segments=(_seg(1, 12.345, 34.567),),
            shared_match_ids=frozenset({"secret-match-b"}),
        )
        m_b = _make_match(
            "secret-match-b",
            segments=(_seg(1, 14.0, 32.0),),
            shared_match_ids=frozenset({"secret-match-a"}),
        )

        with caplog.at_level(logging.DEBUG, logger="dna_analysis.triangulation"):
            find_triangulation_groups([m_a, m_b])

        log_text = "\n".join(rec.getMessage() for rec in caplog.records)
        assert "secret-match-a" not in log_text
        assert "secret-match-b" not in log_text
        # Аггрегаты вида "1 groups", "2 matches" допустимы.


# --- bayes_boost -------------------------------------------------------------


class TestBayesBoost:
    """Heuristic-policy множителя confidence_boost."""

    @staticmethod
    def _group(member_count: int) -> TriangulationGroup:
        return TriangulationGroup(
            chromosome=1,
            start_cm=10.0,
            end_cm=20.0,
            members=tuple(f"m{i}" for i in range(member_count)),
        )

    def test_pair_triplet_returns_1_2(self) -> None:
        """2 members (single triplet) → 1.2x."""
        assert bayes_boost(self._group(2), tree_relationship=None) == pytest.approx(1.2)
        assert bayes_boost(self._group(2), tree_relationship="2C") == pytest.approx(1.2)

    def test_three_members_no_mrca_returns_1_0(self) -> None:
        """3+ members без known MRCA → 1.0x (no boost)."""
        assert bayes_boost(self._group(3), tree_relationship=None) == pytest.approx(1.0)
        assert bayes_boost(self._group(7), tree_relationship=None) == pytest.approx(1.0)

    def test_three_members_with_mrca_returns_1_5(self) -> None:
        """3+ members c known MRCA → 1.5x (сильный сигнал)."""
        assert bayes_boost(self._group(3), tree_relationship="3C1R") == pytest.approx(1.5)

    def test_endogamy_threshold_overrides_to_0_5(self) -> None:
        """> 10 members → 0.5x (endogamy penalty), даже с MRCA.

        Это эвристика «слишком хорошо, чтобы быть правдой» — Phase 6.5
        заменит на честный IBD2-анализ.
        """
        endo = self._group(ENDOGAMY_MEMBER_COUNT_THRESHOLD + 1)
        assert bayes_boost(endo, tree_relationship=None) == pytest.approx(0.5)
        # Даже наличие tree_relationship не отменяет penalty — это
        # сигнатурный Ashkenazi-pattern, см. ADR-0054.
        assert bayes_boost(endo, tree_relationship="4C") == pytest.approx(0.5)
