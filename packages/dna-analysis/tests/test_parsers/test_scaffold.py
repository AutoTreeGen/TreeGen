"""Sanity-чек скелета parsers/ — все классы импортируются и подчиняются ABC."""

from __future__ import annotations

import pytest
from dna_analysis.parsers import (
    AncestryParser,
    BaseDnaParser,
    FamilyTreeDnaParser,
    MyHeritageParser,
    TwentyThreeAndMeParser,
)

_PARSERS: list[type[BaseDnaParser]] = [
    TwentyThreeAndMeParser,
    AncestryParser,
    MyHeritageParser,
    FamilyTreeDnaParser,
]


@pytest.mark.parametrize("parser_cls", _PARSERS)
def test_parser_subclasses_base(parser_cls: type[BaseDnaParser]) -> None:
    assert issubclass(parser_cls, BaseDnaParser)


@pytest.mark.parametrize("parser_cls", _PARSERS)
def test_detect_returns_false_for_random_text(parser_cls: type[BaseDnaParser]) -> None:
    """Detect должен отвергать произвольный текст без header сигнатуры."""
    assert parser_cls.detect("hello world\nthis is not DNA\n") is False
