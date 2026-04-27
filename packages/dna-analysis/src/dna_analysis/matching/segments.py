"""Поиск shared segments между двумя DnaTest (half-IBD GERMLINE-style).

Алгоритм описан в ADR-0014. Кратко:
    - intersect rsid обоих тестов (cross-platform handling)
    - для каждой autosomal хромосомы (1..22) сортируем общие SNP по позиции
    - скользим по SNP, расширяя сегмент пока genotypes совпадают
      (half-IBD: пара совпадает если есть общий аллель)
    - no-call (NN) пропускаем — не разрывает сегмент, не считается за SNP
    - на mismatch закрываем сегмент; если ≥ min_snps и ≥ min_cm — добавляем

Privacy: см. ADR-0012 + ADR-0014. Логирование — только агрегаты
(сколько сегментов, total cM); никаких rsid / genotypes / позиций
в logs или exception messages. Тесты с caplog проверяют этот invariant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from dna_analysis.genetic_map import GeneticMap
from dna_analysis.models import DnaTest, Genotype, Snp

_LOG: Final = logging.getLogger(__name__)

# Phase 6.1 — только autosomes 1..22 (см. ADR-0014).
_AUTOSOMAL: Final = frozenset(range(1, 23))

# Дефолты согласно ADR-0014 (industry standard).
DEFAULT_MIN_CM: Final = 7.0
DEFAULT_MIN_SNPS: Final = 500


@dataclass(frozen=True, slots=True)
class SharedSegment:
    """Один shared half-IBD сегмент между двумя DnaTest."""

    chromosome: int
    start_bp: int
    end_bp: int
    num_snps: int
    cm_length: float


def find_shared_segments(
    test_a: DnaTest,
    test_b: DnaTest,
    genetic_map: GeneticMap,
    *,
    min_cm: float = DEFAULT_MIN_CM,
    min_snps: int = DEFAULT_MIN_SNPS,
) -> list[SharedSegment]:
    """Возвращает список half-IBD сегментов между двумя DnaTest.

    Сегмент — непрерывный диапазон общих SNP, на котором у обоих тестов
    есть хотя бы один общий аллель в каждой позиции (no-call позиции
    пропускаются). Сегмент попадает в результат если он содержит
    ≥ `min_snps` совпадающих SNP и его генетическая длина (по
    `genetic_map`) ≥ `min_cm`.

    Args:
        test_a: Первый тест.
        test_b: Второй тест.
        genetic_map: HapMap GRCh37 (или совместимая) карта для конвертации
            bp → cM. Если хромосомы нет в карте, она пропускается с warning
            в логах.
        min_cm: Минимальная длина сегмента в cM (default 7.0).
        min_snps: Минимальное количество совпадающих SNP в сегменте
            (default 500).

    Returns:
        Отсортированный по (chromosome, start_bp) список SharedSegment.
        Пустой список если совпадений не найдено или нет общих rsid.
    """
    common_by_chromosome = _index_common_snps(test_a, test_b)
    segments: list[SharedSegment] = []

    for chromosome in sorted(common_by_chromosome.keys()):
        if chromosome not in genetic_map.chromosomes:
            _LOG.debug(
                "skip chromosome %d: not in genetic map (%d common SNPs)",
                chromosome,
                len(common_by_chromosome[chromosome]),
            )
            continue
        chrom_segments = _scan_chromosome(
            chromosome=chromosome,
            common_snps=common_by_chromosome[chromosome],
            genetic_map=genetic_map,
            min_cm=min_cm,
            min_snps=min_snps,
        )
        segments.extend(chrom_segments)

    total_cm = sum(seg.cm_length for seg in segments)
    _LOG.debug("found %d shared segments, total %.2f cM", len(segments), total_cm)
    return segments


def _index_common_snps(test_a: DnaTest, test_b: DnaTest) -> dict[int, list[tuple[Snp, Snp]]]:
    """Группирует общие rsid'ы обоих тестов по chromosome (autosomal only).

    Возвращает dict {chromosome → [(snp_a, snp_b), ...]}, отсортированный
    по позиции внутри каждой хромосомы. Хромосома берётся у test_a;
    если у test_b у того же rsid другая chromosome — позиция считается
    конфликтной и пара отбрасывается (cross-build mismatch).
    """
    by_rsid_a = {snp.rsid: snp for snp in test_a.snps}
    by_rsid_b = {snp.rsid: snp for snp in test_b.snps}
    common_rsids = set(by_rsid_a.keys()) & set(by_rsid_b.keys())

    grouped: dict[int, list[tuple[Snp, Snp]]] = {}
    skipped_chromosome_mismatch = 0
    for rsid in common_rsids:
        snp_a = by_rsid_a[rsid]
        snp_b = by_rsid_b[rsid]
        if snp_a.chromosome != snp_b.chromosome:
            skipped_chromosome_mismatch += 1
            continue
        chrom = int(snp_a.chromosome)
        if chrom not in _AUTOSOMAL:
            continue
        grouped.setdefault(chrom, []).append((snp_a, snp_b))

    for chrom, pairs in grouped.items():
        pairs.sort(key=lambda pair: pair[0].position)
        grouped[chrom] = pairs

    if skipped_chromosome_mismatch:
        _LOG.debug(
            "skipped %d SNPs with cross-test chromosome mismatch",
            skipped_chromosome_mismatch,
        )

    return grouped


def _scan_chromosome(
    *,
    chromosome: int,
    common_snps: list[tuple[Snp, Snp]],
    genetic_map: GeneticMap,
    min_cm: float,
    min_snps: int,
) -> list[SharedSegment]:
    """Сканирует одну хромосому и возвращает qualifying сегменты."""
    segments: list[SharedSegment] = []
    seg_start_pos: int | None = None
    seg_end_pos: int = 0
    seg_snp_count = 0

    for snp_a, snp_b in common_snps:
        match = _half_ibd_match(snp_a.genotype, snp_b.genotype)
        if match is None:
            # No-call — пропускаем без разрыва сегмента.
            continue
        if match:
            if seg_start_pos is None:
                seg_start_pos = snp_a.position
            seg_end_pos = snp_a.position
            seg_snp_count += 1
            continue
        # Mismatch — закрываем текущий сегмент и пытаемся накопить новый.
        _close_segment_if_qualifies(
            chromosome=chromosome,
            seg_start_pos=seg_start_pos,
            seg_end_pos=seg_end_pos,
            seg_snp_count=seg_snp_count,
            genetic_map=genetic_map,
            min_cm=min_cm,
            min_snps=min_snps,
            out=segments,
        )
        seg_start_pos = None
        seg_end_pos = 0
        seg_snp_count = 0

    # Закрываем последний сегмент в конце сканирования.
    _close_segment_if_qualifies(
        chromosome=chromosome,
        seg_start_pos=seg_start_pos,
        seg_end_pos=seg_end_pos,
        seg_snp_count=seg_snp_count,
        genetic_map=genetic_map,
        min_cm=min_cm,
        min_snps=min_snps,
        out=segments,
    )
    return segments


def _close_segment_if_qualifies(
    *,
    chromosome: int,
    seg_start_pos: int | None,
    seg_end_pos: int,
    seg_snp_count: int,
    genetic_map: GeneticMap,
    min_cm: float,
    min_snps: int,
    out: list[SharedSegment],
) -> None:
    """Если открытый сегмент проходит фильтры — добавляет его в out."""
    if seg_start_pos is None or seg_snp_count < min_snps:
        return
    cm_length = genetic_map.physical_to_genetic(
        chromosome, seg_end_pos
    ) - genetic_map.physical_to_genetic(chromosome, seg_start_pos)
    if cm_length < min_cm:
        return
    out.append(
        SharedSegment(
            chromosome=chromosome,
            start_bp=seg_start_pos,
            end_bp=seg_end_pos,
            num_snps=seg_snp_count,
            cm_length=cm_length,
        )
    )


# --- half-IBD match logic ----------------------------------------------------


def _half_ibd_match(g1: Genotype, g2: Genotype) -> bool | None:
    """True если g1 и g2 разделяют хотя бы один аллель.

    None означает "нет данных" (хотя бы один genotype = NN no-call) —
    позиция должна быть пропущена, а не считаться mismatch'ом.
    """
    a1 = _alleles(g1)
    a2 = _alleles(g2)
    if not a1 or not a2:
        return None
    return bool(a1 & a2)


def _alleles(genotype: Genotype) -> frozenset[str]:
    """Множество аллелей в genotype-токене.

    'AA' → {'A'}, 'AC' → {'A', 'C'}, 'A' (hemizygous) → {'A'},
    'II' (insertion homozygote) → {'I'}, 'ID' → {'I', 'D'},
    'NN' (no-call) → пустое множество (caller интерпретирует как ambiguous).
    """
    if genotype is Genotype.NN:
        return frozenset()
    return frozenset(genotype.value)
