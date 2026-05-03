"""Парсеры raw DNA-файлов от direct-to-consumer провайдеров.

Phase 6.0:
    - 23andMe v5 (TSV) — TwentyThreeAndMeParser.
    - AncestryDNA v2 (TSV) — AncestryParser.
Phase 16.1 (vendor coverage extension):
    - MyHeritage CSV — MyHeritageParser.
    - FTDNA Family Finder CSV — FamilyTreeDnaParser.
    - LivingDNA TSV — LivingDnaParser.

Универсальный диспетчер по содержимому — `dna_analysis.dispatcher.parse_raw`.
"""

from __future__ import annotations

from dna_analysis.parsers.ancestry import AncestryParser
from dna_analysis.parsers.base import BaseDnaParser
from dna_analysis.parsers.family_tree_dna import FamilyTreeDnaParser
from dna_analysis.parsers.living_dna import LivingDnaParser
from dna_analysis.parsers.myheritage import MyHeritageParser
from dna_analysis.parsers.twentythreeand_me import TwentyThreeAndMeParser

__all__ = [
    "AncestryParser",
    "BaseDnaParser",
    "FamilyTreeDnaParser",
    "LivingDnaParser",
    "MyHeritageParser",
    "TwentyThreeAndMeParser",
]
