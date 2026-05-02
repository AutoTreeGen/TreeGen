"""Тесты парсера LivingDNA TSV.

Все fixture-данные синтетические (см. tests/_generators.py +
tests/fixtures/synthetic_livingdna.txt). Никаких реальных rsids/genotypes.
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
from dna_analysis.parsers import LivingDnaParser

_HEADER = (
    "# LivingDNA Raw Data Download v1.0.0\n"
    "# Reference build: GRCh37 (hg19)\n"
    "# rsid\tchromosome\tposition\tgenotype\n"
)


def test_parser_detects_livingdna_format(synthetic_livingdna_file: str) -> None:
    assert LivingDnaParser.detect(synthetic_livingdna_file) is True


def test_parser_rejects_unknown_format() -> None:
    assert LivingDnaParser.detect("plain text\nno header\n") is False


def test_parser_does_not_match_23andme_header(synthetic_23andme_file: str) -> None:
    assert LivingDnaParser.detect(synthetic_23andme_file) is False


def test_parser_does_not_match_ancestry_header(synthetic_ancestry_file: str) -> None:
    assert LivingDnaParser.detect(synthetic_ancestry_file) is False


def test_parser_does_not_match_myheritage_header(synthetic_myheritage_file: str) -> None:
    assert LivingDnaParser.detect(synthetic_myheritage_file) is False


def test_parser_does_not_match_ftdna_header(synthetic_ftdna_file: str) -> None:
    assert LivingDnaParser.detect(synthetic_ftdna_file) is False


def test_parser_parses_synthetic_file(synthetic_livingdna_file: str) -> None:
    parser = LivingDnaParser()
    test = parser.parse(synthetic_livingdna_file)

    assert isinstance(test, DnaTest)
    assert test.provider is Provider.LIVING_DNA
    assert test.version == "v1"
    assert test.reference_build is ReferenceBuild.GRCH37
    assert len(test.snps) == 100


def test_parser_skips_comment_lines(synthetic_livingdna_file: str) -> None:
    parser = LivingDnaParser()
    test = parser.parse(synthetic_livingdna_file)
    for snp in test.snps:
        assert re.fullmatch(r"rs\d+", snp.rsid), f"unexpected rsid {snp.rsid!r}"


def test_normalizes_heterozygous_pair_order() -> None:
    """`CA` от LivingDNA должен нормализоваться в Genotype.AC."""
    content = _HEADER + "rs1\t1\t100\tCA\n" + "rs2\t1\t200\tTG\n"
    parser = LivingDnaParser()
    test = parser.parse(content)
    assert test.snps[0].genotype is Genotype.AC
    assert test.snps[1].genotype is Genotype.GT


def test_handles_no_call_genotype() -> None:
    """`--` от LivingDNA → Genotype.NN."""
    content = _HEADER + "rs1\t1\t100\t--\n"
    parser = LivingDnaParser()
    test = parser.parse(content)
    assert test.snps[0].genotype is Genotype.NN


def test_handles_x_y_mt_chromosomes() -> None:
    content = _HEADER + "rs1\tX\t100\tAA\n" + "rs2\tY\t200\tA\n" + "rs3\tMT\t300\tC\n"
    parser = LivingDnaParser()
    test = parser.parse(content)
    assert [snp.chromosome for snp in test.snps] == [
        Chromosome.X,
        Chromosome.Y,
        Chromosome.MT,
    ]


def test_rejects_invalid_chromosome() -> None:
    content = _HEADER + "rs1\t99\t12345\tAA\n"
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "chromosome" in str(exc.value).lower()
    # Privacy: сырое значение "99" НЕ должно быть в сообщении.
    assert "99" not in str(exc.value)


def test_rejects_unknown_genotype() -> None:
    content = _HEADER + "rs1\t1\t12345\tZZ\n"
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "genotype" in str(exc.value).lower()
    # Privacy: сырое значение "ZZ" НЕ должно быть в сообщении.
    assert "ZZ" not in str(exc.value)


def test_rejects_non_integer_position() -> None:
    content = _HEADER + "rs1\t1\tnot_a_number\tAA\n"
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_zero_position() -> None:
    content = _HEADER + "rs1\t1\t0\tAA\n"
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_wrong_column_count() -> None:
    content = _HEADER + "rs1\t1\t12345\n"  # 3 колонки вместо 4
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_file_with_no_data_rows() -> None:
    parser = LivingDnaParser()
    with pytest.raises(DnaParseError, match="no SNP rows"):
        parser.parse(_HEADER)


def test_parse_raises_unsupported_for_non_livingdna_content() -> None:
    parser = LivingDnaParser()
    with pytest.raises(UnsupportedFormatError):
        parser.parse("random text without header\n")


def test_parser_does_not_log_raw_values(
    synthetic_livingdna_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: парсер не должен логировать rsid/genotype/position SNP."""
    parser = LivingDnaParser()
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.parsers.living_dna"):
        parser.parse(synthetic_livingdna_file)

    log_text = "\n".join(record.message for record in caplog.records)
    assert not re.search(r"\brs\d+\b", log_text), f"rsid leaked into logs: {log_text!r}"
    for token in ("AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT", "--"):
        assert token not in log_text, f"genotype {token!r} leaked into logs: {log_text!r}"
