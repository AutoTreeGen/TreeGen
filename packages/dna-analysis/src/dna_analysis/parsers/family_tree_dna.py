"""Stub парсера FamilyTreeDNA Family Finder CSV.

Реализация — Phase 6.1. Сейчас возвращает UnsupportedFormatError, чтобы
fail loud при попытке использования.
"""

from __future__ import annotations

from dna_analysis.errors import UnsupportedFormatError
from dna_analysis.models import DnaTest
from dna_analysis.parsers.base import BaseDnaParser


class FamilyTreeDnaParser(BaseDnaParser):
    """Парсер FTDNA Family Finder raw CSV — заглушка (Phase 6.1)."""

    @classmethod
    def detect(cls, content: str) -> bool:  # noqa: ARG003
        return False

    def parse(self, content: str) -> DnaTest:  # noqa: ARG002
        msg = "FamilyTreeDNA parser not implemented yet (Phase 6.1)"
        raise UnsupportedFormatError(msg)
