"""GEDCOM Fantasy Filter — rule-based fabrication / impossibility detector.

Phase 5.10 / ADR-0077. Closes the GEDCOM Doctor stack (5.6 compat
simulator, 5.8 validator, 5.9 export audit, 5.10 fantasy filter).

**Read-only.** Никогда не мутирует ``GedcomDocument`` или его entities —
все правила возвращают ``FantasyFlag`` объекты, а не правят данные на
месте. Принципиально (см. ADR-0077): пользователю — affordance, не
автомат.

**No ML.** v1 — детерминированные rule-based детекторы. ADR-0077
обосновывает выбор и фиксирует non-goals (no surname/ethnicity
heuristics, no auto-mutation, confidence ≤ 0.95 даже на critical).

Public API:

* :func:`scan_document` — entry-point, прогоняет все enabled rules.
* :class:`FantasyRule` — Protocol для собственных правил.
* :class:`FantasyFlag`, :class:`FantasySeverity` — DTO.
"""

from __future__ import annotations

from gedcom_parser.fantasy.engine import FantasyRule, scan_document
from gedcom_parser.fantasy.types import FantasyFlag, FantasySeverity

__all__ = ["FantasyFlag", "FantasyRule", "FantasySeverity", "scan_document"]
