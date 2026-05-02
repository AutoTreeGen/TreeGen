"""Impossible geography — stub for v1 (deferred per Phase 5.8 brief).

Spec calls for: "Impossible geography: spouse marriage location vs known
residence (defer if no geo data)".

V1 contract: this rule class exists in the registry so the rule_id slot
is reserved and downstream UIs can show "this rule is planned but not
yet active". Returns ``[]`` unconditionally.

When implemented (future Phase 5.8b — needs geocoding + travel-time
heuristic), the contract will be: emit a WARNING when a person's known
residence is incompatible with the marriage place at marriage time
(e.g. residence in Australia but marriage in 1820 Russia, with no migration
event between). Requires ``ParsedPlace`` to carry geo coordinates +
hierarchy — already supported by parser, but reverse-geocoding-derived
travel-cost matrix is the missing piece.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.validator.types import Finding, ValidatorContext


class GeographyImpossibilityRule:
    """Stub for v1 — no geocoding integration yet, always returns []."""

    rule_id: str = "geography_impossibility"

    def check(self, doc: GedcomDocument, ctx: ValidatorContext) -> Iterable[Finding]:  # noqa: ARG002
        # TODO Phase 5.8b: implement once travel-cost heuristic lands.
        return ()


__all__ = ["GeographyImpossibilityRule"]
