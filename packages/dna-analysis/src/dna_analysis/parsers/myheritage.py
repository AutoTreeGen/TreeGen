"""Парсер MyHeritage DNA raw CSV.

Формат документирован MyHeritage в header'е каждого экспорта:
    # MyHeritage DNA raw data.
    # For each SNP, we provide the identifier, chromosome number,
    # base pair position and genotype.
    # The genotype is reported on the forward (+) strand with respect
    # to the human reference build 37.
    RSID,CHROMOSOME,POSITION,RESULT
    "rs4477212","1","82154","AA"
    ...

Reference build определяется по header'у: "build 37" → GRCh37,
"build 38" → GRCh38. Default — GRCh37 (большинство экспортов на 2024).

Genotype-токены — комбинированные (как 23andMe, не как Ancestry):
"AA", "AC", "--" для no-call. Полный набор — см. _GENOTYPE_MAP.

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

_DETECT_SIGNATURE: Final = "# MyHeritage DNA raw data"
_DETECT_HEAD_LINES: Final = 20

_VERSION: Final = "v1"

# CSV header line, который MyHeritage пишет после комментариев.
_CSV_HEADER: Final = "RSID,CHROMOSOME,POSITION,RESULT"

# Map платформенных хромосомных меток в Chromosome enum.
# MyHeritage использует "MT" для митохондриальной ДНК, но в литературе
# встречается также "M" — поддерживаем обе формы для робастности.
_CHROMOSOME_MAP: Final[dict[str, Chromosome]] = {
    **{str(i): Chromosome(i) for i in range(1, 23)},
    "X": Chromosome.X,
    "Y": Chromosome.Y,
    "MT": Chromosome.MT,
    "M": Chromosome.MT,
}

_NO_CALL_TOKEN: Final = "--"

# Все валидные сырые токены genotype от MyHeritage → нормализованный Genotype.
# Гетерозиготы — обе перестановки (MyHeritage, как и 23andMe, не гарантирует
# порядок аллелей, поэтому AC и CA → AC).
_GENOTYPE_MAP: Final[dict[str, Genotype]] = {
    _NO_CALL_TOKEN: Genotype.NN,
    "": Genotype.NN,  # MyHeritage иногда экспортирует пустую строку для no-call.
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


class MyHeritageParser(BaseDnaParser):
    """Парсер MyHeritage raw CSV (4 columns: rsid, chromosome, position, genotype)."""

    @classmethod
    def detect(cls, content: str) -> bool:
        """True, если в первых _DETECT_HEAD_LINES строках есть header-сигнатура."""
        for i, line in enumerate(content.splitlines()):
            if i >= _DETECT_HEAD_LINES:
                return False
            if _DETECT_SIGNATURE in line:
                return True
        return False

    def parse(self, content: str) -> DnaTest:
        if not self.detect(content):
            msg = "content does not look like a MyHeritage raw file"
            raise UnsupportedFormatError(msg)

        reference_build = _detect_reference_build(content)
        snps = _parse_csv_body(content)

        if not snps:
            msg = "no SNP rows found after header"
            raise DnaParseError(msg)

        # SHA-256 prefix для logging — privacy-safe идентификатор файла.
        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        _LOG.debug("parsed %d SNPs from MyHeritage file [%s]", len(snps), file_hash)

        return DnaTest(
            provider=Provider.MYHERITAGE,
            version=_VERSION,
            reference_build=reference_build,
            snps=snps,
        )


def _detect_reference_build(content: str) -> ReferenceBuild:
    """Определяет reference build по header'у MyHeritage.

    "build 38" / "grch38" → GRCh38, иначе GRCh37 (default).
    Смотрим только в первых _DETECT_HEAD_LINES строках.
    """
    for i, raw_line in enumerate(content.splitlines()):
        if i >= _DETECT_HEAD_LINES:
            break
        line_lower = raw_line.lower()
        if "build 38" in line_lower or "grch38" in line_lower:
            return ReferenceBuild.GRCH38
    return ReferenceBuild.GRCH37


def _parse_csv_body(content: str) -> list[dict[str, object]]:
    """Парсит CSV-тело MyHeritage в список kwargs для Snp(...).

    Пропускает comment-строки (`#...`) и сам CSV header. Использует
    `csv.reader` для корректной обработки quoted-полей.
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
        # CSV header — первая non-comment строка с RSID/CHROMOSOME/...
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
