"""GEDCOM diff library — read-only сравнение двух разобранных деревьев.

Phase 5.7a: foundation для GEDCOM Safe Merge (см. ROADMAP §5.7). Сравнивает
два :class:`~gedcom_parser.document.GedcomDocument` и возвращает структурный
diff-report с разделами persons / relations / sources / unknown_tags.

Person matching выполняется через канонический person matcher из
``entity_resolution.persons.person_match_score`` (ADR-0015) — composite
weighted score по surname phonetic + name Levenshtein + birth year ±2 +
birth place fuzzy + sex hard-filter. Threshold 0.85+ → likely duplicate
(см. ADR-0015 §«Алгоритмы / Persons»).

Этот PR — только diff. Conflict resolution UI и apply-to-target — в 5.7b/c.

Пример::

    >>> from gedcom_parser.diff import diff_gedcoms, DiffOptions
    >>> from gedcom_parser import parse_document_file
    >>> left = parse_document_file("ancestry-export.ged")
    >>> right = parse_document_file("myheritage-export.ged")
    >>> report = diff_gedcoms(left, right, DiffOptions())
    >>> report.persons_added, len(report.persons_modified)
"""

from __future__ import annotations

from gedcom_parser.diff.engine import diff_gedcoms
from gedcom_parser.diff.types import (
    DiffOptions,
    DiffReport,
    FamilyChange,
    FieldChange,
    PersonChange,
    SourceChange,
    UnknownTagChange,
)

__all__ = [
    "DiffOptions",
    "DiffReport",
    "FamilyChange",
    "FieldChange",
    "PersonChange",
    "SourceChange",
    "UnknownTagChange",
    "diff_gedcoms",
]
