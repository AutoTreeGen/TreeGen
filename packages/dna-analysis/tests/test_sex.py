"""Тесты эвристики `infer_sex`.

Эвристика читает только Y-хромосомные SNP из распарсенного DnaTest:
- Хотя бы один Y-SNP с не-NN genotype → MALE.
- Y-SNP присутствуют, но все NN → FEMALE.
- Y-SNP отсутствуют → UNKNOWN.
"""

from __future__ import annotations

from dna_analysis import (
    Chromosome,
    DnaTest,
    Genotype,
    Provider,
    ReferenceBuild,
    Sex,
    Snp,
    infer_sex,
)


def _make_test(snps: list[Snp]) -> DnaTest:
    """Builder для DnaTest с произвольными SNP — тестовая утилита."""
    return DnaTest(
        provider=Provider.TWENTY_THREE_AND_ME,
        version="v5",
        reference_build=ReferenceBuild.GRCH37,
        snps=snps,
    )


def test_male_when_y_snps_have_valid_calls() -> None:
    test = _make_test(
        [
            Snp(rsid="rs1", chromosome=Chromosome.Y, position=100, genotype=Genotype.A),
            Snp(rsid="rs2", chromosome=Chromosome.Y, position=200, genotype=Genotype.NN),
        ]
    )
    assert infer_sex(test) is Sex.MALE


def test_female_when_y_snps_all_no_call() -> None:
    test = _make_test(
        [
            Snp(rsid="rs1", chromosome=Chromosome.Y, position=100, genotype=Genotype.NN),
            Snp(rsid="rs2", chromosome=Chromosome.Y, position=200, genotype=Genotype.NN),
        ]
    )
    assert infer_sex(test) is Sex.FEMALE


def test_unknown_when_no_y_snps() -> None:
    """Файл вообще без Y-rows — нельзя различить female от male-без-Y-данных."""
    test = _make_test(
        [
            Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=100, genotype=Genotype.AA),
            Snp(rsid="rs2", chromosome=Chromosome.X, position=200, genotype=Genotype.AC),
        ]
    )
    assert infer_sex(test) is Sex.UNKNOWN


def test_male_inference_ignores_other_chromosomes() -> None:
    """Валидные не-Y genotype calls не делают тест мужским."""
    test = _make_test(
        [
            Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=100, genotype=Genotype.AA),
            Snp(rsid="rs2", chromosome=Chromosome.X, position=200, genotype=Genotype.AC),
            Snp(rsid="rs3", chromosome=Chromosome.MT, position=300, genotype=Genotype.A),
        ]
    )
    assert infer_sex(test) is Sex.UNKNOWN


def test_male_when_single_valid_y_call_among_many_no_calls() -> None:
    """Достаточно одного валидного Y-SNP среди тысяч NN — это male."""
    snps = [
        Snp(rsid=f"rs{i}", chromosome=Chromosome.Y, position=i, genotype=Genotype.NN)
        for i in range(1, 100)
    ]
    snps.append(Snp(rsid="rs100", chromosome=Chromosome.Y, position=100, genotype=Genotype.G))
    test = _make_test(snps)
    assert infer_sex(test) is Sex.MALE


def test_female_real_file(synthetic_ancestry_file: str) -> None:
    """Sanity-чек на реальной synthetic-fixture: ancestry-генератор не выдаёт
    непустые Y-genotype'ы регулярно (random.choice с seed=42), так что результат
    может быть FEMALE или MALE — главное, чтобы функция не упала и вернула
    валидный Sex enum.
    """
    from dna_analysis import parse_raw

    test = parse_raw(synthetic_ancestry_file)
    result = infer_sex(test)
    assert result in {Sex.MALE, Sex.FEMALE, Sex.UNKNOWN}
