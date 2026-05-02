"""Default fantasy rule registry (Phase 5.10).

Один файл per логическая группа правил (date-impossibility, parent-age,
parent-alive, structural). 12 default правил v1 в стабильном порядке —
порядок влияет на сортировку flags в API-ответе.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gedcom_parser.fantasy.rules.date_impossibility import (
    BirthAfterDeathRule,
    ImpossibleLifespanRule,
)
from gedcom_parser.fantasy.rules.descent_chain import (
    DirectDescentFromPre1500NamedFigureRule,
    SuspiciousGenerationalCompressionRule,
)
from gedcom_parser.fantasy.rules.parent_age import (
    ParentTooOldAtBirthRule,
    ParentTooYoungAtBirthRule,
)
from gedcom_parser.fantasy.rules.parent_alive import (
    DeathBeforeChildBirthFatherRule,
    DeathBeforeChildBirthMotherRule,
)
from gedcom_parser.fantasy.rules.parent_order import (
    ChildBeforeParentBirthRule,
)
from gedcom_parser.fantasy.rules.structural import (
    CircularDescentRule,
    IdenticalBirthYearSiblingsExcessRule,
    MassFabricatedBranchRule,
)

if TYPE_CHECKING:
    from gedcom_parser.fantasy.engine import FantasyRule


def default_rules() -> list[FantasyRule]:
    """Стабильный список default-enabled правил.

    Order rationale: critical/structural первыми, дальше по убыванию
    типичной severity. UI группирует по severity, но stable order даёт
    детерминированный diff между scan'ами.
    """
    return [
        # CRITICAL impossibilities
        BirthAfterDeathRule(),
        ChildBeforeParentBirthRule(),
        DeathBeforeChildBirthMotherRule(),
        DeathBeforeChildBirthFatherRule(),
        CircularDescentRule(),
        # HIGH (likely fabrication)
        ImpossibleLifespanRule(),
        ParentTooYoungAtBirthRule(),
        SuspiciousGenerationalCompressionRule(),
        DirectDescentFromPre1500NamedFigureRule(),
        MassFabricatedBranchRule(),
        # WARNING (anomaly)
        ParentTooOldAtBirthRule(),
        IdenticalBirthYearSiblingsExcessRule(),
    ]


__all__ = [
    "BirthAfterDeathRule",
    "ChildBeforeParentBirthRule",
    "CircularDescentRule",
    "DeathBeforeChildBirthFatherRule",
    "DeathBeforeChildBirthMotherRule",
    "DirectDescentFromPre1500NamedFigureRule",
    "IdenticalBirthYearSiblingsExcessRule",
    "ImpossibleLifespanRule",
    "MassFabricatedBranchRule",
    "ParentTooOldAtBirthRule",
    "ParentTooYoungAtBirthRule",
    "SuspiciousGenerationalCompressionRule",
    "default_rules",
]
