"""Парсер FamilyTreeDNA Family Finder raw CSV.

Формат документирован FTDNA в каждом экспорте — plain CSV без
comment-блока:

    RSID,CHROMOSOME,POSITION,RESULT
    "rs3094315","1","752566","AA"
    "rs4040617","1","779322","AG"
    ...

Reference build: GRCh37 для архивных kits (~2017 и старше),
GRCh38 для современных. По умолчанию GRCh37 — FTDNA не пишет build
в файл, переопределение возможно через caller (Phase 16.1-pt2).

Detection: тот же 4-column CSV header, что и у MyHeritage. Чтобы их
не путать, FTDNA-detect требует ОТСУТСТВИЕ MyHeritage-сигнатуры
("# MyHeritage DNA raw data") в первых _DETECT_HEAD_LINES строках.

Genotype-токены — комбинированные (как 23andMe / MyHeritage).

Приватность (см. ADR-0012):
- В логах — только агрегаты (количество SNP, prefix sha256-хеша файла).
- DnaParseError содержит line number и тип ошибки, но НЕ raw value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
from typing import Final

from dna_analysis.errors import DnaParseError, UnsupportedFormatError
from dna_analysis.models import Chromosome, DnaTest, Genotype, Provider, ReferenceBuild
from dna_analysis.parsers.base import BaseDnaParser

_LOG: Final = logging.getLogger(__name__)

_DETECT_HEAD_LINES: Final = 20

# Header-сигнатуры конкурирующих CSV-вендоров — если найдены, файл НЕ FTDNA.
_MYHERITAGE_SIGNATURE: Final = "# MyHeritage DNA raw data"

_CSV_HEADER: Final = "RSID,CHROMOSOME,POSITION,RESULT"

# FTDNA не указывает reference build в файле; современные kits — GRCh38,
# архивные — GRCh37. Default — GRCh37 (consensus с другими 4-column CSV
# вендорами Phase 6.0). Уточнение build — отдельная задача (16.1-pt2).
_REFERENCE_BUILD: Final = ReferenceBuild.GRCH37
_VERSION: Final = "v1"

# Map платформенных хромосомных меток в Chromosome enum.
_CHROMOSOME_MAP: Final[dict[str, Chromosome]] = {
    **{str(i): Chromosome(i) for i in range(1, 23)},
    "X": Chromosome.X,
    "Y": Chromosome.Y,
    "MT": Chromosome.MT,
    "M": Chromosome.MT,
}

_NO_CALL_TOKEN: Final = "--"

_GENOTYPE_MAP: Final[dict[str, Genotype]] = {
    _NO_CALL_TOKEN: Genotype.NN,
    "": Genotype.NN,
    # Hemizygous (Y / MT / male X).
    "A": Genotype.A,
    "C": Genotype.C,
    "G": Genotype.G,
    "T": Genotype.T,
    # Indel-вызовы.
    "II": Genotype.II,
    "DD": Genotype.DD,
    "ID": Genotype.ID,
    "DI": Genotype.ID,
    # Гомозиготы.
    "AA": Genotype.AA,
    "CC": Genotype.CC,
    "GG": Genotype.GG,
    "TT": Genotype.TT,
    # Гетерозиготы — обе перестановки → один canonical Genotype.
    "AC": Genotype.AC,
    "CA": Genotype.AC,
    "AG": Genotype.AG,
    "GA": Genotype.AG,
    "AT": Genotype.AT,
    "TA": Genotype.AT,
    "CG": Genotype.CG,
    "GC": Genotype.CG,
    "CT": Genotype.CT,
    "TC": Genotype.CT,
    "GT": Genotype.GT,
    "TG": Genotype.GT,
}

_EXPECTED_COLUMN_COUNT: Final = 4


class FamilyTreeDnaParser(BaseDnaParser):
    """Парсер FTDNA Family Finder raw CSV (4 columns, no comment block)."""

    @classmethod
    def detect(cls, content: str) -> bool:
        """True, если первая non-empty строка — `RSID,CHROMOSOME,POSITION,RESULT`
        и в первых _DETECT_HEAD_LINES строках нет MyHeritage-сигнатуры.
        """
        first_data_line: str | None = None
        for i, raw_line in enumerate(content.splitlines()):
            if i >= _DETECT_HEAD_LINES:
                break
            line = raw_line.strip()
            if _MYHERITAGE_SIGNATURE in line:
                # Файл — MyHeritage, не FTDNA.
                return False
            if not line or line.startswith("#"):
                continue
            if first_data_line is None:
                first_data_line = line
        if first_data_line is None:
            return False
        # Нормализуем (убираем quotes для устойчивости).
        normalized = first_data_line.replace('"', "").upper()
        return normalized == _CSV_HEADER

    def parse(self, content: str) -> DnaTest:
        if not self.detect(content):
            msg = "content does not look like a FTDNA Family Finder raw file"
            raise UnsupportedFormatError(msg)

        snps = _parse_csv_body(content)
        if not snps:
            msg = "no SNP rows found after header"
            raise DnaParseError(msg)

        # SHA-256 prefix для logging — privacy-safe идентификатор файла.
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        _LOG.debug("parsed %d SNPs from FTDNA file [%s]", len(snps), file_hash)

        return DnaTest(
            provider=Provider.FTDNA,
            version=_VERSION,
            reference_build=_REFERENCE_BUILD,
            snps=snps,
        )


def _parse_csv_body(content: str) -> list[dict[str, object]]:
    """Парсит CSV-тело FTDNA в список kwargs для Snp(...).

    Пропускает comment-строки (если они есть — теоретически могут быть
    в архивных kits) и сам CSV header. Использует `csv.reader` для
    корректной обработки quoted-полей.
    """
    snps: list[dict[str, object]] = []
    seen_csv_header = False

    reader = csv.reader(io.StringIO(content))
    for line_idx, parts in enumerate(reader, start=1):
        if not parts:
            continue
        first = parts[0]
        if first.startswith("#"):
            continue
        if not seen_csv_header:
            joined = ",".join(parts).upper()
            if joined == _CSV_HEADER:
                seen_csv_header = True
                continue
            msg = "missing CSV header (expected RSID,CHROMOSOME,POSITION,RESULT)"
            raise DnaParseError(msg, line_number=line_idx)
        snps.append(_parse_snp_row(parts, line_idx))

    return snps


def _parse_snp_row(parts: list[str], line_number: int) -> dict[str, object]:
    """Парсит одну SNP-строку CSV в kwargs для Snp(...)."""
    if len(parts) != _EXPECTED_COLUMN_COUNT:
        msg = f"expected {_EXPECTED_COLUMN_COUNT} comma-separated columns, got {len(parts)}"
        raise DnaParseError(msg, line_number=line_number)

    rsid, chrom_token, position_token, genotype_token = parts

    if not rsid:
        msg = "empty rsid"
        raise DnaParseError(msg, line_number=line_number)

    chromosome = _CHROMOSOME_MAP.get(chrom_token)
    if chromosome is None:
        msg = "invalid chromosome"
        raise DnaParseError(msg, line_number=line_number)

    try:
        position = int(position_token)
    except ValueError as exc:
        msg = "invalid position (not an integer)"
        raise DnaParseError(msg, line_number=line_number) from exc
    if position <= 0:
        msg = "invalid position (must be positive)"
        raise DnaParseError(msg, line_number=line_number)

    genotype = _GENOTYPE_MAP.get(genotype_token)
    if genotype is None:
        msg = "invalid genotype"
        raise DnaParseError(msg, line_number=line_number)

    return {
        "rsid": rsid,
        "chromosome": chromosome,
        "position": position,
        "genotype": genotype,
    }
