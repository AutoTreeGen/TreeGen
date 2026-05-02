"""Тесты парсера MyHeritage CSV.

Все fixture-данные синтетические (см. tests/_generators.py +
tests/fixtures/synthetic_myheritage.csv). Никаких реальных rsids/genotypes.
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
from dna_analysis.parsers import MyHeritageParser

_HEADER_BUILD37 = (
    "# MyHeritage DNA raw data.\n"
    "# The genotype is reported on the forward (+) strand with respect "
    "to the human reference build 37.\n"
    "RSID,CHROMOSOME,POSITION,RESULT\n"
)


def test_parser_detects_myheritage_format(synthetic_myheritage_file: str) -> None:
    assert MyHeritageParser.detect(synthetic_myheritage_file) is True


def test_parser_rejects_unknown_format() -> None:
    assert MyHeritageParser.detect("plain text\nno header\n") is False


def test_parser_does_not_match_23andme_header(synthetic_23andme_file: str) -> None:
    assert MyHeritageParser.detect(synthetic_23andme_file) is False


def test_parser_does_not_match_ancestry_header(synthetic_ancestry_file: str) -> None:
    assert MyHeritageParser.detect(synthetic_ancestry_file) is False


def test_parser_parses_synthetic_file(synthetic_myheritage_file: str) -> None:
    parser = MyHeritageParser()
    test = parser.parse(synthetic_myheritage_file)

    assert isinstance(test, DnaTest)
    assert test.provider is Provider.MYHERITAGE
    assert test.version == "v1"
    assert test.reference_build is ReferenceBuild.GRCH37
    assert len(test.snps) == 100


def test_parser_skips_comment_lines(synthetic_myheritage_file: str) -> None:
    parser = MyHeritageParser()
    test = parser.parse(synthetic_myheritage_file)
    for snp in test.snps:
        assert re.fullmatch(r"rs\d+", snp.rsid), f"unexpected rsid {snp.rsid!r}"


def test_parses_unquoted_csv_too() -> None:
    """MyHeritage в основном пишет quoted, но парсер обязан принимать и unquoted."""
    content = _HEADER_BUILD37 + "rs1,1,100,AA\n" + "rs2,2,200,AC\n"
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert [snp.genotype for snp in test.snps] == [Genotype.AA, Genotype.AC]


def test_normalizes_heterozygous_alleles_lex_sorted() -> None:
    """CA → AC, TG → GT — обе перестановки схлопываются в canonical Genotype."""
    content = (
        _HEADER_BUILD37
        + '"rs1","1","100","AC"\n'
        + '"rs2","2","200","CA"\n'
        + '"rs3","3","300","TG"\n'
    )
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert test.snps[0].genotype is Genotype.AC
    assert test.snps[1].genotype is Genotype.AC
    assert test.snps[2].genotype is Genotype.GT


def test_handles_no_call_genotype() -> None:
    """`--` и пустая строка → Genotype.NN."""
    content = _HEADER_BUILD37 + '"rs1","1","100","--"\n' + '"rs2","2","200",""\n'
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert all(snp.genotype is Genotype.NN for snp in test.snps)


def test_handles_x_y_mt_chromosomes() -> None:
    content = (
        _HEADER_BUILD37
        + '"rs1","X","100","A"\n'
        + '"rs2","Y","200","C"\n'
        + '"rs3","MT","300","G"\n'
        + '"rs4","M","400","T"\n'
    )
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert [snp.chromosome for snp in test.snps] == [
        Chromosome.X,
        Chromosome.Y,
        Chromosome.MT,
        Chromosome.MT,
    ]


def test_detects_grch38_build_from_header() -> None:
    """Header содержит "build 38" → ReferenceBuild.GRCH38."""
    content = (
        "# MyHeritage DNA raw data.\n"
        "# The genotype is reported on the forward (+) strand with respect "
        "to the human reference build 38.\n"
        "RSID,CHROMOSOME,POSITION,RESULT\n"
        '"rs1","1","100","AA"\n'
    )
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert test.reference_build is ReferenceBuild.GRCH38


def test_defaults_to_grch37_when_build_unspecified() -> None:
    """Header без build → ReferenceBuild.GRCH37 (default)."""
    content = '# MyHeritage DNA raw data.\nRSID,CHROMOSOME,POSITION,RESULT\n"rs1","1","100","AA"\n'
    parser = MyHeritageParser()
    test = parser.parse(content)
    assert test.reference_build is ReferenceBuild.GRCH37


def test_rejects_invalid_chromosome() -> None:
    content = _HEADER_BUILD37 + '"rs1","99","100","AA"\n'
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "chromosome" in str(exc.value).lower()
    # Privacy: сырое значение "99" НЕ должно быть в сообщении.
    assert "99" not in str(exc.value)


def test_rejects_unknown_genotype() -> None:
    content = _HEADER_BUILD37 + '"rs1","1","100","ZZ"\n'
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError) as exc:
        parser.parse(content)
    assert "genotype" in str(exc.value).lower()
    # Privacy: сырое значение "ZZ" НЕ должно быть в сообщении.
    assert "ZZ" not in str(exc.value)


def test_rejects_non_integer_position() -> None:
    content = _HEADER_BUILD37 + '"rs1","1","not_a_number","AA"\n'
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_zero_position() -> None:
    content = _HEADER_BUILD37 + '"rs1","1","0","AA"\n'
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_wrong_column_count() -> None:
    content = _HEADER_BUILD37 + '"rs1","1","100"\n'  # 3 колонки вместо 4
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError):
        parser.parse(content)


def test_rejects_missing_csv_header() -> None:
    """Файл без `RSID,CHROMOSOME,POSITION,RESULT` после comment'ов — fail loud."""
    content = (
        "# MyHeritage DNA raw data.\n"
        '"rs1","1","100","AA"\n'  # сразу данные, без CSV-header
    )
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError, match="CSV header"):
        parser.parse(content)


def test_rejects_file_with_no_data_rows() -> None:
    parser = MyHeritageParser()
    with pytest.raises(DnaParseError, match="no SNP rows"):
        parser.parse(_HEADER_BUILD37)


def test_parse_raises_unsupported_for_non_myheritage_content() -> None:
    parser = MyHeritageParser()
    with pytest.raises(UnsupportedFormatError):
        parser.parse("random text without header\n")


def test_parser_does_not_log_raw_values(
    synthetic_myheritage_file: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Privacy guard: парсер не должен логировать rsid/genotype/position SNP."""
    parser = MyHeritageParser()
    with caplog.at_level(logging.DEBUG, logger="dna_analysis.parsers.myheritage"):
        parser.parse(synthetic_myheritage_file)

    log_text = "\n".join(record.message for record in caplog.records)
    assert not re.search(r"\brs\d+\b", log_text), f"rsid leaked into logs: {log_text!r}"
    for token in ("AA", "AC", "AG", "AT", "CC", "CG", "CT", "GG", "GT", "TT"):
        assert token not in log_text, f"genotype {token!r} leaked into logs: {log_text!r}"
