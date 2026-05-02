"""Structural rules — graph-level / aggregate detection (Phase 5.10).

* :class:`CircularDescentRule` — person is their own ancestor (graph cycle).
  CRITICAL.
* :class:`IdenticalBirthYearSiblingsExcessRule` — >3 siblings same birth
  year (twins / quintuplets are real but not 4+ from one mother). WARNING.
* :class:`MassFabricatedBranchRule` — subtree of >20 persons all sharing
  identical birth-year pattern AND zero source citations. HIGH.

Все эти правила работают на graph / aggregate level, в отличие от
date-impossibility / parent-age, которые per-person.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from gedcom_parser.fantasy.types import (
    MAX_CONFIDENCE,
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)
from gedcom_parser.validator._date_utils import birth_year

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Person


# Brief: > 3 одинаковых birth year для siblings уже подозрительно
# (даже quintuplets — 5 детей одной матери — крайне редкое явление,
# и они почти всегда с medical-документацией).
_MAX_SAME_YEAR_SIBLINGS = 3

# Mass-fabrication thresholds.
_MASS_BRANCH_MIN_SIZE = 20
_MASS_BRANCH_MAX_DISTINCT_BIRTH_YEARS = 3


class CircularDescentRule:
    """Person is their own ancestor — critical (graph cycle)."""

    rule_id: str = "circular_descent"
    default_severity: FantasySeverity = FantasySeverity.CRITICAL

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        # Build child→parents map один раз.
        parents_of: dict[str, set[str]] = defaultdict(set)
        for family in doc.families.values():
            ps: set[str] = set()
            if family.husband_xref:
                ps.add(family.husband_xref)
            if family.wife_xref:
                ps.add(family.wife_xref)
            for child_xref in family.children_xrefs:
                parents_of[child_xref].update(ps)

        # DFS до cycle detection. Каждую персону пробуем как root.
        # Cycle detection через path-set; глубина ограничена реальным
        # размером дерева (десятки тысяч в худшем случае — приемлемо).
        seen_cycles: set[frozenset[str]] = set()
        for start_xref in doc.persons:
            cycle = self._find_cycle(start_xref, parents_of)
            if cycle is None:
                continue
            cycle_key = frozenset(cycle)
            if cycle_key in seen_cycles:
                continue
            seen_cycles.add(cycle_key)
            # Эмитим один flag на caché — субъект = «первая в cycle» персона
            # (стабильный sort'ом по xref для детерминизма).
            subject_xref = sorted(cycle)[0]
            yield FantasyFlag(
                rule_id=self.rule_id,
                severity=FantasySeverity.CRITICAL,
                confidence=MAX_CONFIDENCE,
                reason=(
                    f"Circular descent detected: persons {sorted(cycle)} form an "
                    "ancestor cycle. A person cannot be their own ancestor."
                ),
                person_xref=subject_xref,
                evidence={
                    "cycle_xrefs": sorted(cycle),
                    "cycle_length": len(cycle),
                },
                suggested_action=(
                    "Identify and break the loop — usually a duplicate-person "
                    "merge that should not have happened."
                ),
            )

    def _find_cycle(
        self,
        start: str,
        parents_of: dict[str, set[str]],
    ) -> list[str] | None:
        """DFS от ``start`` вверх по родителям; вернуть cycle xrefs или None."""
        path: list[str] = []
        path_set: set[str] = set()
        stack: list[tuple[str, int]] = [(start, 0)]
        # iterative DFS с записью текущего пути:
        while stack:
            node, depth = stack[-1]
            # Prune path до depth (когда возвращаемся вверх).
            while len(path) > depth:
                removed = path.pop()
                path_set.discard(removed)
            if node in path_set:
                # Cycle: вырезаем сегмент пути от первого вхождения.
                idx = path.index(node)
                return [*path[idx:], node]
            path.append(node)
            path_set.add(node)
            ps = parents_of.get(node)
            if not ps:
                stack.pop()
                continue
            # push parents (sorted for determinism)
            stack.pop()
            for parent in sorted(ps):
                stack.append((parent, depth + 1))
        return None


class IdenticalBirthYearSiblingsExcessRule:
    """>3 siblings sharing identical birth year — warning."""

    rule_id: str = "identical_birth_year_siblings_excess"
    default_severity: FantasySeverity = FantasySeverity.WARNING

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        for family in doc.families.values():
            year_groups: dict[int, list[str]] = defaultdict(list)
            for child_xref in family.children_xrefs:
                child = doc.get_person(child_xref)
                if child is None:
                    continue
                yr = birth_year(child)
                if yr is None:
                    continue
                year_groups[yr].append(child_xref)
            for year, sibs in year_groups.items():
                if len(sibs) <= _MAX_SAME_YEAR_SIBLINGS:
                    continue
                yield FantasyFlag(
                    rule_id=self.rule_id,
                    severity=FantasySeverity.WARNING,
                    confidence=0.7,
                    reason=(
                        f"Family {family.xref_id} has {len(sibs)} siblings born in "
                        f"year {year}: {sorted(sibs)}. "
                        f"Threshold is {_MAX_SAME_YEAR_SIBLINGS} (twins/triplets are "
                        "common; 4+ siblings same year is implausible without medical "
                        "documentation)."
                    ),
                    family_xref=family.xref_id,
                    evidence={
                        "shared_birth_year": year,
                        "sibling_xrefs": sorted(sibs),
                        "count": len(sibs),
                        "threshold": _MAX_SAME_YEAR_SIBLINGS,
                    },
                    suggested_action=(
                        "Likely date-imputation artifact: many tools fill missing "
                        "birth years with parent's marriage year + 1. Verify each."
                    ),
                )


class MassFabricatedBranchRule:
    """Subtree of >20 persons all sharing identical birth-year pattern + zero citations.

    Heuristic for viral-fabrication imports: a single user uploads a wholesale
    invented branch where every person is "circa 1850" with no source.
    """

    rule_id: str = "mass_fabricated_branch"
    default_severity: FantasySeverity = FantasySeverity.HIGH

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        # Find connected subtrees through family / sibling links.
        adj: dict[str, set[str]] = defaultdict(set)
        for family in doc.families.values():
            members: set[str] = set(family.children_xrefs)
            if family.husband_xref:
                members.add(family.husband_xref)
            if family.wife_xref:
                members.add(family.wife_xref)
            for a in members:
                for b in members:
                    if a != b:
                        adj[a].add(b)

        visited: set[str] = set()
        for start in doc.persons:
            if start in visited:
                continue
            component = self._bfs(start, adj)
            visited.update(component)
            if len(component) < _MASS_BRANCH_MIN_SIZE:
                continue
            yield from self._inspect_component(doc, component)

    def _bfs(self, start: str, adj: dict[str, set[str]]) -> set[str]:
        """Find all xrefs reachable from start via family-membership edges."""
        seen: set[str] = {start}
        frontier: list[str] = [start]
        while frontier:
            node = frontier.pop()
            for nb in adj.get(node, ()):
                if nb not in seen:
                    seen.add(nb)
                    frontier.append(nb)
        return seen

    def _inspect_component(
        self,
        doc: GedcomDocument,
        component: set[str],
    ) -> Iterable[FantasyFlag]:
        persons: list[Person] = []
        for xref in component:
            p = doc.get_person(xref)
            if p is not None:
                persons.append(p)
        # Distinct birth years — пропускаем None.
        distinct_years: set[int] = set()
        any_with_citations = False
        for p in persons:
            yr = birth_year(p)
            if yr is not None:
                distinct_years.add(yr)
            if p.citations or p.sources_xrefs:
                any_with_citations = True
        # Гипотеза: ≤3 distinct birth years AND zero citations across whole subtree.
        if len(distinct_years) > _MASS_BRANCH_MAX_DISTINCT_BIRTH_YEARS:
            return
        if any_with_citations:
            return
        sample = sorted(component)[:10]
        yield FantasyFlag(
            rule_id=self.rule_id,
            severity=FantasySeverity.HIGH,
            confidence=0.8,
            reason=(
                f"Connected subtree of {len(component)} persons shares only "
                f"{len(distinct_years)} distinct birth years and has zero source "
                "citations across the entire branch. Hallmark of mass-imported "
                "fabrication."
            ),
            evidence={
                "component_size": len(persons),
                "distinct_birth_years": sorted(distinct_years),
                "sample_xrefs": sample,
                "size_threshold": _MASS_BRANCH_MIN_SIZE,
                "year_diversity_threshold": _MASS_BRANCH_MAX_DISTINCT_BIRTH_YEARS,
            },
            person_xref=sample[0] if sample else None,
            suggested_action=(
                "Review the branch as a whole. If all persons trace to a single "
                "uncited online tree, consider removing or marking as unverified."
            ),
        )


__all__ = [
    "CircularDescentRule",
    "IdenticalBirthYearSiblingsExcessRule",
    "MassFabricatedBranchRule",
]
