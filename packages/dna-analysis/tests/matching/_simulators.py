"""Симуляторы DnaTest для matching-тестов.

Все генерации детерминированы (Random(seed=42)), используют только
синтетические rsid (rs1..rsN) и синтетические позиции в bp-диапазоне
chr22 (16 050 000..51 244 566) — соответствует test fixture
genetic map. Никаких реальных DNA-данных. См. ADR-0012 + ADR-0014.
"""

from __future__ import annotations

import random

from dna_analysis.models import (
    Chromosome,
    DnaTest,
    Genotype,
    Provider,
    ReferenceBuild,
    Snp,
)

# Соответствует диапазону chr22 в test fixture genetic map.
_CHR22_START_BP = 16_050_000
_CHR22_END_BP = 51_244_566

_DIPLOID_GENOTYPES = (
    Genotype.AA,
    Genotype.AC,
    Genotype.AG,
    Genotype.AT,
    Genotype.CC,
    Genotype.CG,
    Genotype.CT,
    Genotype.GG,
    Genotype.GT,
    Genotype.TT,
)


def _positions_chr22(num_snps: int) -> list[int]:
    """Уникальные строго возрастающие позиции на chr22."""
    span = _CHR22_END_BP - _CHR22_START_BP
    step = span / (num_snps + 1)
    return [int(_CHR22_START_BP + step * (i + 1)) for i in range(num_snps)]


def _make_test(provider: Provider, snps: list[Snp]) -> DnaTest:
    return DnaTest(
        provider=provider,
        version="v5",
        reference_build=ReferenceBuild.GRCH37,
        snps=snps,
    )


def _alleles_of(genotype: Genotype) -> tuple[str, str]:
    """Раскладывает diploid genotype-токен в пару аллелей."""
    value = genotype.value
    return value[0], value[1]


def simulate_independent_pair(*, num_snps: int, seed: int = 42) -> tuple[DnaTest, DnaTest]:
    """Два независимо случайных теста — proxy для unrelated пары.

    Каждый SNP получает независимый случайный diploid genotype в обоих
    тестах. На длинных участках случайно совпадающие сегменты крайне
    маловероятны (≪ 7 cM), что и проверяется в test_two_unrelated_tests.
    """
    rng = random.Random(seed)
    positions = _positions_chr22(num_snps)
    snps_a: list[Snp] = []
    snps_b: list[Snp] = []
    for i, pos in enumerate(positions, start=1):
        rsid = f"rs{i}"
        snps_a.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=rng.choice(_DIPLOID_GENOTYPES),
            )
        )
        snps_b.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=rng.choice(_DIPLOID_GENOTYPES),
            )
        )
    return (
        _make_test(Provider.TWENTY_THREE_AND_ME, snps_a),
        _make_test(Provider.ANCESTRY, snps_b),
    )


def simulate_identical_pair(*, num_snps: int, seed: int = 42) -> tuple[DnaTest, DnaTest]:
    """Два теста с идентичными genotypes (identical-twin / self-match)."""
    rng = random.Random(seed)
    positions = _positions_chr22(num_snps)
    snps: list[Snp] = []
    for i, pos in enumerate(positions, start=1):
        snps.append(
            Snp(
                rsid=f"rs{i}",
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=rng.choice(_DIPLOID_GENOTYPES),
            )
        )
    test_a = _make_test(Provider.TWENTY_THREE_AND_ME, snps)
    test_b = _make_test(Provider.ANCESTRY, snps)
    return test_a, test_b


def simulate_parent_child_pair(*, num_snps: int, seed: int = 42) -> tuple[DnaTest, DnaTest]:
    """Симуляция parent-child: ребёнок наследует один аллель от parent
    в каждой позиции, второй — случайный.

    На каждой позиции у parent и child будет хотя бы один общий аллель,
    то есть half-IBD совпадает по всему геному (по chr22 в нашей
    fixture).
    """
    rng = random.Random(seed)
    positions = _positions_chr22(num_snps)
    parent_snps: list[Snp] = []
    child_snps: list[Snp] = []
    for i, pos in enumerate(positions, start=1):
        rsid = f"rs{i}"
        parent_g = rng.choice(_DIPLOID_GENOTYPES)
        parent_snps.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=parent_g,
            )
        )
        # Child наследует один из двух parent-аллелей + независимый
        # второй (от другого, неизвестного нам родителя).
        inherited_allele = rng.choice(_alleles_of(parent_g))
        other_allele = rng.choice(("A", "C", "G", "T"))
        child_pair = "".join(sorted([inherited_allele, other_allele]))
        # Гомозиготная пара ("AA") и гетерозиготная ("AC") обе валидны.
        child_snps.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=Genotype(child_pair),
            )
        )
    return (
        _make_test(Provider.TWENTY_THREE_AND_ME, parent_snps),
        _make_test(Provider.ANCESTRY, child_snps),
    )


def simulate_segment_shared_pair(
    *,
    num_snps: int,
    shared_ranges: list[tuple[int, int]],
    seed: int = 42,
) -> tuple[DnaTest, DnaTest]:
    """Тест: внутри `shared_ranges` (по индексу SNP) — parent-child-style
    sharing; вне — независимые случайные genotypes.

    Используется для simulating distant cousins (несколько коротких
    блоков sharing'а на фоне случайного шума).
    """
    rng = random.Random(seed)
    positions = _positions_chr22(num_snps)
    in_shared: list[bool] = [False] * num_snps
    for lo, hi in shared_ranges:
        for idx in range(lo, hi):
            in_shared[idx] = True

    snps_a: list[Snp] = []
    snps_b: list[Snp] = []
    for i, pos in enumerate(positions, start=1):
        rsid = f"rs{i}"
        g_a = rng.choice(_DIPLOID_GENOTYPES)
        snps_a.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=g_a,
            )
        )
        if in_shared[i - 1]:
            inherited = rng.choice(_alleles_of(g_a))
            other = rng.choice(("A", "C", "G", "T"))
            pair = "".join(sorted([inherited, other]))
            g_b = Genotype(pair)
        else:
            g_b = rng.choice(_DIPLOID_GENOTYPES)
        snps_b.append(
            Snp(
                rsid=rsid,
                chromosome=Chromosome.CHR_22,
                position=pos,
                genotype=g_b,
            )
        )
    return (
        _make_test(Provider.TWENTY_THREE_AND_ME, snps_a),
        _make_test(Provider.ANCESTRY, snps_b),
    )
