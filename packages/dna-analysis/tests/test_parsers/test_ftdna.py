"""Тесты парсера FTDNA Family Finder CSV.

Все fixture-данные синтетические (см. tests/_generators.py +
tests/fixtures/synthetic_ftdna.csv). Никаких реальных rsids/genotypes.
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
from dna_analysis.parsers import FamilyTreeDnaParser

_HEADER = "RSID,CHROMOSOME,POSITION,RESULT\n"


def test_parser_detects_ftdna_format(synthetic_ftdna_file: str) -> None:
    assert FamilyTreeDnaParser.detect(synthetic_ftdna_file) is True


def test_parser_rejects_unknown_format() -> None:
    assert FamilyTreeDnaParser.detect("plain text\nno header\n") is False


def test_parser_does_not_match_myheritage_header(synthetic_myheritage_file: str) -> None:
    """FTDNA detect должен отвергнуть MyHeritage-файл (тот же CSV header,
    но с # MyHeritage DNA raw data в comment-блоке)."""
    assert FamilyTreeDnaParser.detect(synthetic_myheritage_file) is False


def test_parser_does_not_match_ancestry_header(synthetic_ancestry_file: str) -> None:
    assert FamilyTreeDnaParser.detect(synthetic_ancestry_file) is False


def test_parser_does_not_match_23andme_header(synthetic_23andme_file: str) -> None:
    assert FamilyTreeDnaParser.detect(synthetic_23andme_file) is False


def test_parser_parses_synthetic_file(synthetic_ftdna_file: str) -> None:
    parser = FamilyTreeDnaParser()
    test = parser.parse(synthetic_ftdna_file)

    assert isinstance(test, DnaTest)
    assert test.provider is Provider.FTDNA
    assert test.version == "v1"
    assert test.reference_build is ReferenceBuild.GRCH37
    assert len(test.snps) == 100


def test_parser_skips_csv_header(synthetic_ftdna_file: str) -> None:
    parser = FamilyTreeDnaParser()
    test = parser.parse(synthetic_ftdna_file)
    for snp in test.snps:
        assert re.fullmatch(r"rs\d+", snp.rsid), f"unexpected rsid {snp.rsid!r}"


def test_parses_unquoted_csv_too() -> None:
    """FTDNA в основном пишет quoted, но парсер обязан принимать и unquoted."""
    content = _HEADER + "rs1,1,100,AA\nrs2,2,200,AC\n"
    parser = FamilyTreeDnaParser()
    test = parser.parse(content)
    assert [snp.genotype for snp in test.snps] == [Genotype.AA, Genotype.AC]


def test_normalizes_heterozygous_alleles_lex_sorted() -> None:
    content = (
        _HEADER + '"rs1","1","100","AC"\n' + '"rs2","2","200","CA"\n' + '"rs3","3","300","TG"\n'
    )
    parser = FamilyTreeDnaParser()
    test = parser.parse(content)
    assert test.snps[0].genotype is Genotype.AC
    assert test.snps[1].genotype is Genotype.AC
    assert test.snps[2].genotype is Genotype.GT


def test_handles_no_call_genotype() -> None:
    content = _HEADER + '"rs1","1","100","--"\n' + '"rs2","2","200",""\n'
    parser = FamilyTreeDnaParser()
    test = parser.parse(content)
    assert all(snp.genotype is Genotype.NN for snp in test.snps)


def test_handles_x_y_mt_chromosomes() -> None:
    content = _HEADER + '"rs1","X","100","A"\n' + '"rs2","Y","200","C"\n' + '"rs3","MT","300","G"\n'
    parser = FamilyTreeDnaParser()
    test = parser.parse(content)
    assert [snp.chromosome for snp in test.snps] == [Chromosome.X, Chromosome.Y, Chromosome.MT]


def test_rejects_invalid_chromosome() -> None:
    content = _HEADER + '"rs1","99","100","AA"\n'
    parser = FamilyTreeDnaParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "chromosome" in str(exc.value).lower()
    assert "99" not in str(exc.value)


def test_rejects_unknown_genotype() -> None:
    content = _HEADER + '"rs1","1","100","ZZ"\n'
    parser = FamilyTreeDnaParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "genotype" in str(exc.value).lower()
    assert "ZZ" not in str(exc.value)


def test_rejects_non_integer_position() -> None:
    content = _HEADER + '"rs1","1","not_a_number","AA"\n'
    parser = FamilyTreeDnaParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_wrong_column_count() -> None:
    content = _HEADER + '"rs1","1","100"\n'
    parser = FamilyTreeDnaParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_file_with_no_data_rows() -> None:
    parser = FamilyTreeDnaParser()
    with pytest.raises(DnaParseError, match="no SNP rows"):
        parser.parse(_HEADER)


def test_parse_raises_unsupported_for_non_ftdna_content() -> None:
    parser = FamilyTreeDnaParser()
    with pytest.raises(UnsupportedFormatError):
        parser.parse("random text without header\n")


def test_parser_does_not_log_raw_values(
    synthetic_ftdna_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: парсер не должен логировать rsid/genotype/position SNP."""
    parser = FamilyTreeDnaParser()
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.parsers.family_tree_dna"):
        parser.parse(synthetic_ftdna_file)

    log_text = "\n".join(record.message for record in caplog.records)
    assert not re.search(r"\brs\d+\b", log_text), f"rsid leaked into logs: {log_text!r}"
    for token in ("AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT"):
        assert token not in log_text, f"genotype {token!r} leaked into logs: {log_text!r}"
