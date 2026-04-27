"""Тесты Pydantic-моделей dna-analysis."""

from __future__ import annotations

import pytest
from dna_analysis import (
    Chromosome,
    DnaTest,
    Genotype,
    Provider,
    ReferenceBuild,
    Snp,
)
from pydantic import ValidationError


def test_snp_constructs_with_valid_fields() -> None:
    snp = Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=12345, genotype=Genotype.AA)
    assert snp.rsid == "rs1"
    assert snp.chromosome == Chromosome.CHR_1
    assert snp.position == 12345
    assert snp.genotype is Genotype.AA


def test_snp_is_frozen() -> None:
    snp = Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=10, genotype=Genotype.AA)
    with pytest.raises(ValidationError):
        snp.position = 20  # type: ignore[misc]


def test_snp_rejects_zero_position() -> None:
    with pytest.raises(ValidationError):
        Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=0, genotype=Genotype.AA)


def test_snp_rejects_unknown_chromosome_value() -> None:
    with pytest.raises(ValidationError):
        Snp(rsid="rs1", chromosome=99, position=10, genotype=Genotype.AA)  # type: ignore[arg-type]


def test_snp_rejects_unknown_genotype_value() -> None:
    with pytest.raises(ValidationError):
        Snp(rsid="rs1", chromosome=Chromosome.CHR_1, position=10, genotype="ZZ")  # type: ignore[arg-type]


def test_snp_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Snp(  # type: ignore[call-arg]
            rsid="rs1",
            chromosome=Chromosome.CHR_1,
            position=10,
            genotype=Genotype.AA,
            unexpected_field="x",
        )


def test_dna_test_requires_at_least_one_snp() -> None:
    with pytest.raises(ValidationError):
        DnaTest(
            provider=Provider.TWENTY_THREE_AND_ME,
            version="v5",
            reference_build=ReferenceBuild.GRCH37,
            snps=[],
        )


def test_dna_test_constructs_and_freezes() -> None:
    snp = Snp(rsid="rs1", chromosome=Chromosome.X, position=1_000_000, genotype=Genotype.NN)
    test = DnaTest(
        provider=Provider.ANCESTRY,
        version="v2",
        reference_build=ReferenceBuild.GRCH37,
        snps=[snp],
    )
    assert test.provider is Provider.ANCESTRY
    assert test.snps == [snp]
    with pytest.raises(ValidationError):
        test.version = "v6"  # type: ignore[misc]


@pytest.mark.parametrize("provider", list(Provider))
def test_provider_enum_values_are_lowercase_brand_names(provider: Provider) -> None:
    assert provider.value == provider.value.lower()


@pytest.mark.parametrize(
    ("chromosome", "expected"),
    [
        (Chromosome.CHR_1, 1),
        (Chromosome.CHR_22, 22),
        (Chromosome.X, 23),
        (Chromosome.Y, 24),
        (Chromosome.MT, 25),
    ],
)
def test_chromosome_int_values(chromosome: Chromosome, expected: int) -> None:
    assert int(chromosome) == expected
