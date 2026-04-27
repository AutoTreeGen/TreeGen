"""Парсер 23andMe v5 raw данных (Phase 6.0 — реализация в Task 3)."""

from __future__ import annotations

from dna_analysis.errors import UnsupportedFormatError
from dna_analysis.models import DnaTest
from dna_analysis.parsers.base import BaseDnaParser


class TwentyThreeAndMeParser(BaseDnaParser):
    """Парсер 23andMe v5 raw TSV (GRCh37).

    Phase 6.0 scaffold: detect()/parse() заглушены, реализация
    в feat/phase-6.0-23andme-parser (Task 3).
    """

    @classmethod
    def detect(cls, content: str) -> bool:  # noqa: ARG003
        return False

    def parse(self, content: str) -> DnaTest:  # noqa: ARG002
        msg = "23andMe parser not implemented yet (Phase 6.0 Task 3)"
        raise UnsupportedFormatError(msg)
