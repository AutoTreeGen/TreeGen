"""Validator rule classes (Phase 5.8).

One file per rule. Public entry: :func:`default_rules` returns a fresh
list of all built-in rule instances in deterministic order. Order is
stable so finding-lists between runs diff cleanly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.validator.rules.broken_xref import BrokenCrossRefRule
from gedcom_parser.validator.rules.duplicate_child import DuplicateChildRule
from gedcom_parser.validator.rules.duplicate_spouse import DuplicateSpouseRule
from gedcom_parser.validator.rules.geography import GeographyImpossibilityRule
from gedcom_parser.validator.rules.missing_xref import MissingXrefRule
from gedcom_parser.validator.rules.parent_age import (
    FatherAgeAtChildBirthRule,
    MotherAgeAtChildBirthRule,
)
from gedcom_parser.validator.rules.parent_alive import ChildBirthAfterParentDeathRule
from gedcom_parser.validator.rules.same_sex_spouse import SameSexSpousePairRule
from gedcom_parser.validator.rules.self_consistency import DeathBeforeBirthRule

if TYPE_CHECKING:
    from gedcom_parser.validator.engine import ValidatorRule


def default_rules() -> list[ValidatorRule]:
    """Return all built-in validator rules, fresh instances, stable order.

    Order rationale: structural / parse-level issues first (missing xref,
    broken xref) so users fix loud problems before hunting subtle date
    inconsistencies. Domain rules sorted by likely-frequency on real data
    (duplicate-child commoner than same-sex-spouse).
    """
    return [
        MissingXrefRule(),
        BrokenCrossRefRule(),
        DeathBeforeBirthRule(),
        MotherAgeAtChildBirthRule(),
        FatherAgeAtChildBirthRule(),
        ChildBirthAfterParentDeathRule(),
        DuplicateChildRule(),
        DuplicateSpouseRule(),
        SameSexSpousePairRule(),
        GeographyImpossibilityRule(),
    ]


__all__ = [
    "BrokenCrossRefRule",
    "ChildBirthAfterParentDeathRule",
    "DeathBeforeBirthRule",
    "DuplicateChildRule",
    "DuplicateSpouseRule",
    "FatherAgeAtChildBirthRule",
    "GeographyImpossibilityRule",
    "MissingXrefRule",
    "MotherAgeAtChildBirthRule",
    "SameSexSpousePairRule",
    "default_rules",
]
