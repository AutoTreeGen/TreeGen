"""Парсер AncestryDNA v2 raw данных (Phase 6.0 — реализация в Task 4)."""

from __future__ import annotations

from dna_analysis.errors import UnsupportedFormatError
from dna_analysis.models import DnaTest
from dna_analysis.parsers.base import BaseDnaParser


class AncestryParser(BaseDnaParser):
    """Парсер AncestryDNA v2 raw TSV (GRCh37, 5 columns с allele1+allele2).

    Phase 6.0 scaffold: detect()/parse() заглушены, реализация
    в feat/phase-6.0-ancestry-parser (Task 4).
    """

    @classmethod
    def detect(cls, content: str) -> bool:  # noqa: ARG003
        return False

    def parse(self, content: str) -> DnaTest:  # noqa: ARG002
        msg = "AncestryDNA parser not implemented yet (Phase 6.0 Task 4)"
        raise UnsupportedFormatError(msg)
