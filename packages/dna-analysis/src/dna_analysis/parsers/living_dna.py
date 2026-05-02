"""Парсер LivingDNA raw TSV данных.

Формат (документирован LivingDNA в header'е каждого экспорта):

    # LivingDNA Raw Data Download v1.0.0
    # Reference build: GRCh37 (hg19)
    # rsid	chromosome	position	genotype
    rs548049170	1	69869	TT
    rs9283150	1	565508	AA
    ...

Структурно — точная копия 23andMe v5: TSV, 4 колонки, `#`-комментарии,
column-header `# rsid	chromosome	position	genotype`. Отличается только
header-сигнатурой `# LivingDNA`. Reference build — GRCh37.

Приватность (см. ADR-0012):
- В логах — только агрегаты (количество SNP, prefix sha256-хеша файла).
- DnaParseError содержит line number и тип ошибки, но НЕ raw value.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Final

from dna_analysis.errors import DnaParseError, UnsupportedFormatError
from dna_analysis.models import Chromosome, DnaTest, Genotype, Provider, ReferenceBuild
from dna_analysis.parsers.base import BaseDnaParser

_LOG: Final = logging.getLogger(__name__)

_DETECT_SIGNATURE: Final = "# LivingDNA"
_DETECT_HEAD_LINES: Final = 20

_REFERENCE_BUILD: Final = ReferenceBuild.GRCH37
_VERSION: Final = "v1"

_CHROMOSOME_MAP: Final[dict[str, Chromosome]] = {
    **{str(i): Chromosome(i) for i in range(1, 23)},
    "X": Chromosome.X,
    "Y": Chromosome.Y,
    "MT": Chromosome.MT,
}

_NO_CALL_TOKEN: Final = "--"

_GENOTYPE_MAP: Final[dict[str, Genotype]] = {
    _NO_CALL_TOKEN: Genotype.NN,
    "A": Genotype.A,
    "C": Genotype.C,
    "G": Genotype.G,
    "T": Genotype.T,
    "II": Genotype.II,
    "DD": Genotype.DD,
    "ID": Genotype.ID,
    "DI": Genotype.ID,
    "AA": Genotype.AA,
    "CC": Genotype.CC,
    "GG": Genotype.GG,
    "TT": Genotype.TT,
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


class LivingDnaParser(BaseDnaParser):
    """Парсер LivingDNA raw TSV (GRCh37, 4 columns: rsid, chromosome, position, genotype)."""

    @classmethod
    def detect(cls, content: str) -> bool:
        """True, если в первых _DETECT_HEAD_LINES строках есть `# LivingDNA` сигнатура."""
        for i, line in enumerate(content.splitlines()):
            if i >= _DETECT_HEAD_LINES:
                return False
            if _DETECT_SIGNATURE in line:
                return True
        return False

    def parse(self, content: str) -> DnaTest:
        if not self.detect(content):
            msg = "content does not look like a LivingDNA raw file"
            raise UnsupportedFormatError(msg)

        snps: list[dict[str, object]] = []
        for line_idx, raw_line in enumerate(content.splitlines(), start=1):
            line = raw_line.rstrip("\r\n")
            if not line or line.startswith("#"):
                continue
            snps.append(_parse_snp_row(line, line_idx))

        if not snps:
            msg = "no SNP rows found after header"
            raise DnaParseError(msg)

        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
        _LOG.debug("parsed %d SNPs from LivingDNA file [%s]", len(snps), file_hash)

        return DnaTest(
            provider=Provider.LIVING_DNA,
            version=_VERSION,
            reference_build=_REFERENCE_BUILD,
            snps=snps,
        )


def _parse_snp_row(line: str, line_number: int) -> dict[str, object]:
    """Парсит одну SNP-строку LivingDNA TSV в kwargs для Snp(...)."""
    parts = line.split("\t")
    if len(parts) != _EXPECTED_COLUMN_COUNT:
        msg = f"expected {_EXPECTED_COLUMN_COUNT} tab-separated columns, got {len(parts)}"
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
