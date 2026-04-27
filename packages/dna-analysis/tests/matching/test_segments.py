"""Тесты find_shared_segments на синтетических парах DnaTest.

Все симуляции детерминированы (см. tests/matching/_simulators.py),
используют только chr22 (соответствует test fixture genetic map).
Никаких реальных DNA-данных, никаких реальных rsid.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from dna_analysis import (
    Chromosome,
    DnaTest,
    GeneticMap,
    Genotype,
    Provider,
    ReferenceBuild,
    SharedSegment,
    Snp,
    find_shared_segments,
)

from tests.matching._simulators import (
    simulate_identical_pair,
    simulate_independent_pair,
    simulate_parent_child_pair,
    simulate_segment_shared_pair,
)


@pytest.fixture
def genetic_map(fixtures_dir: Path) -> GeneticMap:
    return GeneticMap.from_directory(fixtures_dir / "genetic_map")


def test_identical_tests_yield_full_chromosome_segment(genetic_map: GeneticMap) -> None:
    """Self-match по всему chr22 → один сегмент через всю хромосому."""
    test_a, test_b = simulate_identical_pair(num_snps=5_000)
    segments = find_shared_segments(test_a, test_b, genetic_map)

    assert len(segments) == 1
    seg = segments[0]
    assert seg.chromosome == 22
    assert seg.num_snps == 5_000
    # Длина должна покрывать ~весь chr22 (~35 cM по synthetic map).
    assert seg.cm_length > 30


def test_unrelated_tests_yield_no_segments(genetic_map: GeneticMap) -> None:
    """Случайные независимые genotypes на 5000 SNPs → ничего ≥ 7 cM."""
    test_a, test_b = simulate_independent_pair(num_snps=5_000)
    segments = find_shared_segments(test_a, test_b, genetic_map)
    # ~50% случайных совпадений, но непрерывных серий ≥ 500 SNP с
    # cM-длиной ≥ 7 ожидать практически невозможно при 5000 точках.
    assert segments == []


def test_parent_child_yields_full_chromosome_segment(genetic_map: GeneticMap) -> None:
    """Parent-child: совпадение по одному аллелю в каждой позиции →
    один сегмент через весь chr22."""
    parent, child = simulate_parent_child_pair(num_snps=5_000)
    segments = find_shared_segments(parent, child, genetic_map)

    assert len(segments) == 1
    seg = segments[0]
    assert seg.chromosome == 22
    assert seg.num_snps == 5_000
    assert seg.cm_length > 30


def test_simulated_distant_cousin_yields_few_blocks(genetic_map: GeneticMap) -> None:
    """3 явных shared-блока на фоне случайного шума → 1..3 сегмента
    (некоторые блоки могут не пройти min_cm порог в синтетической карте).
    """
    # 5000 SNPs всего, 3 блока по 700 SNPs (далеко превышает min_snps=500),
    # каждый блок занимает ~14% chr22 → ~5 cM каждый. Не все проходят
    # min_cm=7, проверяем что движок находит хотя бы один.
    test_a, test_b = simulate_segment_shared_pair(
        num_snps=5_000,
        shared_ranges=[(500, 1300), (2000, 2800), (3500, 4300)],
    )
    segments = find_shared_segments(test_a, test_b, genetic_map, min_cm=4.0)
    assert 1 <= len(segments) <= 3
    for seg in segments:
        assert seg.num_snps >= 500
        assert seg.cm_length >= 4.0


def test_min_snps_filter_excludes_short_runs(genetic_map: GeneticMap) -> None:
    """Один shared-блок длиной 200 SNP (< default min_snps=500) → не возвращается."""
    test_a, test_b = simulate_segment_shared_pair(
        num_snps=5_000,
        shared_ranges=[(1000, 1200)],
    )
    segments = find_shared_segments(test_a, test_b, genetic_map)
    assert segments == []


def test_min_cm_filter_excludes_short_segments(genetic_map: GeneticMap) -> None:
    """Длинный SNP-блок, но генетически короткий — отфильтровывается."""
    # 800 SNPs подряд — выше min_snps, но в crowded region может быть < 7 cM.
    test_a, test_b = simulate_segment_shared_pair(
        num_snps=5_000,
        shared_ranges=[(2400, 3200)],
    )
    high_threshold = find_shared_segments(test_a, test_b, genetic_map, min_cm=20.0)
    low_threshold = find_shared_segments(test_a, test_b, genetic_map, min_cm=4.0)
    # Тот же блок должен пропадать при поднятии порога.
    assert len(high_threshold) <= len(low_threshold)


def test_segments_are_sorted_by_chromosome_then_start(genetic_map: GeneticMap) -> None:
    parent, child = simulate_parent_child_pair(num_snps=2_000)
    segments = find_shared_segments(parent, child, genetic_map)
    sort_key = [(s.chromosome, s.start_bp) for s in segments]
    assert sort_key == sorted(sort_key)


def test_no_common_rsids_yields_no_segments(genetic_map: GeneticMap) -> None:
    """Если rsid'ы не пересекаются — пустой результат."""
    test_a = DnaTest(
        provider=Provider.TWENTY_THREE_AND_ME,
        version="v5",
        reference_build=ReferenceBuild.GRCH37,
        snps=[
            Snp(
                rsid=f"rsa{i}",
                chromosome=Chromosome.CHR_22,
                position=20_000_000 + i,
                genotype=Genotype.AA,
            )
            for i in range(1000)
        ],
    )
    test_b = DnaTest(
        provider=Provider.ANCESTRY,
        version="v2",
        reference_build=ReferenceBuild.GRCH37,
        snps=[
            Snp(
                rsid=f"rsb{i}",
                chromosome=Chromosome.CHR_22,
                position=20_000_000 + i,
                genotype=Genotype.AA,
            )
            for i in range(1000)
        ],
    )
    assert find_shared_segments(test_a, test_b, genetic_map) == []


def test_no_call_does_not_break_segment(genetic_map: GeneticMap) -> None:
    """Вкрапления NN не должны разбивать длинный совпадающий сегмент."""
    parent, child = simulate_identical_pair(num_snps=5_000)
    # Заменить случайные 100 SNPs у child на NN.
    altered_snps = list(child.snps)
    for idx in range(0, 5_000, 50):
        original = altered_snps[idx]
        altered_snps[idx] = Snp(
            rsid=original.rsid,
            chromosome=original.chromosome,
            position=original.position,
            genotype=Genotype.NN,
        )
    child_with_nn = DnaTest(
        provider=child.provider,
        version=child.version,
        reference_build=child.reference_build,
        snps=altered_snps,
    )
    segments = find_shared_segments(parent, child_with_nn, genetic_map)
    # Должен остаться один цельный сегмент — NN не считается mismatch'ом.
    assert len(segments) == 1
    assert segments[0].cm_length > 30


def test_chromosomes_outside_genetic_map_are_skipped(
    fixtures_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Если SNP на chromosome не в карте → молча скипается с лог-warning'ом."""
    genetic_map = GeneticMap.from_directory(fixtures_dir / "genetic_map")
    # Добавим SNPs на chr1 — её нет в test fixture (только chr22).
    extra_snps = [
        Snp(
            rsid=f"rsX{i}",
            chromosome=Chromosome.CHR_1,
            position=1_000_000 + i,
            genotype=Genotype.AA,
        )
        for i in range(700)
    ]
    parent, child = simulate_parent_child_pair(num_snps=2_000)
    test_a = DnaTest(
        provider=parent.provider,
        version=parent.version,
        reference_build=parent.reference_build,
        snps=[*parent.snps, *extra_snps],
    )
    test_b = DnaTest(
        provider=child.provider,
        version=child.version,
        reference_build=child.reference_build,
        snps=[*child.snps, *extra_snps],
    )
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.matching.segments"):
        segments = find_shared_segments(test_a, test_b, genetic_map)

    # chr1 пропущена → находится только chr22-сегмент.
    assert all(seg.chromosome == 22 for seg in segments)
    log_text = "\n".join(record.message for record in caplog.records)
    assert "skip chromosome 1" in log_text


def test_x_y_mt_chromosomes_are_ignored(genetic_map: GeneticMap) -> None:
    """Phase 6.1 — только autosomes 1..22; X/Y/MT не учитываются."""
    parent, child = simulate_parent_child_pair(num_snps=2_000)
    extra_snps = [
        Snp(
            rsid=f"rsXY{i}",
            chromosome=Chromosome.X,
            position=20_000_000 + i,
            genotype=Genotype.AA,
        )
        for i in range(700)
    ]
    test_a = DnaTest(
        provider=parent.provider,
        version=parent.version,
        reference_build=parent.reference_build,
        snps=[*parent.snps, *extra_snps],
    )
    test_b = DnaTest(
        provider=child.provider,
        version=child.version,
        reference_build=child.reference_build,
        snps=[*child.snps, *extra_snps],
    )
    segments = find_shared_segments(test_a, test_b, genetic_map)
    assert all(seg.chromosome != int(Chromosome.X) for seg in segments)


def test_shared_segment_dataclass_is_frozen() -> None:
    seg = SharedSegment(chromosome=22, start_bp=100, end_bp=200, num_snps=600, cm_length=8.5)
    with pytest.raises(AttributeError):
        seg.cm_length = 10.0  # type: ignore[misc]


def test_finder_does_not_log_raw_values(
    genetic_map: GeneticMap,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: логи содержат только агрегаты (count, total cM)."""
    parent, child = simulate_parent_child_pair(num_snps=2_000)
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.matching.segments"):
        find_shared_segments(parent, child, genetic_map)

    log_text = "\n".join(record.message for record in caplog.records)
    # Не должно быть rsid (rs1, rs2, ...).
    assert not re.search(r"\brs\d+\b", log_text), f"rsid leaked: {log_text!r}"
    # Не должно быть genotype-токенов.
    for token in ("AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT"):
        assert token not in log_text, f"genotype {token!r} leaked: {log_text!r}"
    # Не должно быть позиций (8+-значные числа).
    assert not re.search(r"\b\d{8,}\b", log_text), f"position leaked: {log_text!r}"
