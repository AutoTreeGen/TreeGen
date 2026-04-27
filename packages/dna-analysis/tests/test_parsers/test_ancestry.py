"""Тесты парсера AncestryDNA v2.

Все fixture-данные синтетические (см. tests/_generators.py +
tests/fixtures/synthetic_ancestry.txt). Никаких реальных rsids/genotypes.
"""

from __future__ import annotations

import logging
import re

import pytest
from dna_analysis import (
    Chromosome,
    DnaParseError,
    DnaTest,
    Genotype,
    Provider,
    ReferenceBuild,
    UnsupportedFormatError,
)
from dna_analysis.parsers import AncestryParser


def test_parser_detects_ancestry_format(synthetic_ancestry_file: str) -> None:
    assert AncestryParser.detect(synthetic_ancestry_file) is True


def test_parser_rejects_unknown_format() -> None:
    assert AncestryParser.detect("plain text\nno header\n") is False


def test_parser_does_not_match_23andme_header(synthetic_23andme_file: str) -> None:
    assert AncestryParser.detect(synthetic_23andme_file) is False


def test_parser_parses_synthetic_file(synthetic_ancestry_file: str) -> None:
    parser = AncestryParser()
    test = parser.parse(synthetic_ancestry_file)

    assert isinstance(test, DnaTest)
    assert test.provider is Provider.ANCESTRY
    assert test.version == "v2"
    assert test.reference_build is ReferenceBuild.GRCH37
    assert len(test.snps) == 100  # synthetic generator выдаёт 100 SNP


def test_parser_skips_comment_lines(synthetic_ancestry_file: str) -> None:
    parser = AncestryParser()
    test = parser.parse(synthetic_ancestry_file)
    for snp in test.snps:
        assert re.fullmatch(r"rs\d+", snp.rsid), f"unexpected rsid {snp.rsid!r}"


def test_ancestry_combines_two_alleles_homozygous() -> None:
    """Allele1 + allele2 = AA, CC, GG, TT — прямой Genotype."""
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t100\tA\tA\n"
        "rs2\t2\t200\tC\tC\n"
        "rs3\t3\t300\tG\tG\n"
        "rs4\t4\t400\tT\tT\n"
    )
    parser = AncestryParser()
    test = parser.parse(content)

    assert [snp.genotype for snp in test.snps] == [
        Genotype.AA,
        Genotype.CC,
        Genotype.GG,
        Genotype.TT,
    ]


def test_ancestry_combines_two_alleles_heterozygous() -> None:
    """Allele1 + allele2, в любом порядке — нормализуется в lex-sorted Genotype."""
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t100\tA\tC\n"
        "rs2\t2\t200\tC\tA\n"
        "rs3\t3\t300\tT\tG\n"
    )
    parser = AncestryParser()
    test = parser.parse(content)

    # Все три должны схлопнуться в lex-sorted представление.
    assert test.snps[0].genotype is Genotype.AC
    assert test.snps[1].genotype is Genotype.AC  # CA нормализован в AC
    assert test.snps[2].genotype is Genotype.GT  # TG нормализован в GT


def test_ancestry_handles_zero_zero_no_call() -> None:
    """`0\\t0` от Ancestry → Genotype.NN."""
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t100\t0\t0\n"
    )
    parser = AncestryParser()
    test = parser.parse(content)

    assert len(test.snps) == 1
    assert test.snps[0].genotype is Genotype.NN


def test_ancestry_handles_x_y_mt_chromosomes() -> None:
    """Ancestry кодирует X/Y/MT как 23/24/25."""
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t23\t100\tA\tA\n"
        "rs2\t24\t200\tC\tC\n"
        "rs3\t25\t300\tG\tG\n"
    )
    parser = AncestryParser()
    test = parser.parse(content)

    assert [snp.chromosome for snp in test.snps] == [Chromosome.X, Chromosome.Y, Chromosome.MT]


def test_parser_rejects_half_no_call() -> None:
    """Ancestry экспортирует либо оба, либо ни одного `0` — половинный no-call недопустим."""
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t100\tA\t0\n"
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3


def test_parser_rejects_invalid_chromosome() -> None:
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t99\t12345\tA\tA\n"
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3
    assert "chromosome" in str(exc.value).lower()
    # Privacy: сырое значение "99" НЕ должно быть в сообщении.
    assert "99" not in str(exc.value)


def test_parser_rejects_unknown_allele() -> None:
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t12345\tZ\tA\n"
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3
    # Privacy: сырое значение "Z" НЕ должно быть в сообщении.
    assert "'Z'" not in str(exc.value)


def test_parser_rejects_non_integer_position() -> None:
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\tnot_a_number\tA\tA\n"
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3


def test_parser_rejects_zero_position() -> None:
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t0\tA\tA\n"
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3


def test_parser_rejects_wrong_column_count() -> None:
    content = (
        "#AncestryDNA Raw DNA Data Download\n"
        "#rsid\tchromosome\tposition\tallele1\tallele2\n"
        "rs1\t1\t12345\tA\n"  # 4 колонки вместо 5
    )
    parser = AncestryParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert exc.value.line_number == 3


def test_parser_rejects_file_with_no_data_rows() -> None:
    content = "#AncestryDNA Raw DNA Data Download\n#rsid\tchromosome\tposition\tallele1\tallele2\n"
    parser = AncestryParser()
    with pytest.raises(DnaParseError, match="no SNP rows"):
        parser.parse(content)


def test_parse_raises_unsupported_for_non_ancestry_content() -> None:
    parser = AncestryParser()
    with pytest.raises(UnsupportedFormatError):
        parser.parse("random text without header\n")


def test_parser_does_not_log_raw_values(
    synthetic_ancestry_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: парсер не должен логировать rsid/genotype/position SNP."""
    parser = AncestryParser()
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.parsers.ancestry"):
        parser.parse(synthetic_ancestry_file)

    log_text = "\n".join(record.message for record in caplog.records)
    # Не должно быть rsid (например `rs42`).
    assert not re.search(r"\brs\d+\b", log_text), f"rsid leaked into logs: {log_text!r}"
    # Не должно быть genotype-токенов.
    for token in ("AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT"):
        assert token not in log_text, f"genotype {token!r} leaked into logs: {log_text!r}"
