"""Descent-chain heuristics — generational compression + named-figure claim.

* :class:`SuspiciousGenerationalCompressionRule` — chain of ≥5 generations
  с avg generational gap <15 years. HIGH.
* :class:`DirectDescentFromPre1500NamedFigureRule` — claim of direct line
  до historical figure без source citations on intermediate nodes. HIGH.

Anchor list (Charlemagne, Genghis Khan, etc.) загружается из
``data/known_fabrication_anchors.yaml`` — отдельный список для лёгкого
обновления без code change. Anti-drift: список — это **не** ethnic /
surname filter, а narrow whitelist of viral fabrication anchors,
multi-researcher confirmed.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from importlib import resources
from typing import TYPE_CHECKING

from gedcom_parser.fantasy.types import (
    FantasyContext,
    FantasyFlag,
    FantasySeverity,
)
from gedcom_parser.validator._date_utils import birth_year

if TYPE_CHECKING:
    from collections.abc import Iterable

    from gedcom_parser.document import GedcomDocument
    from gedcom_parser.entities import Person

_LOG = logging.getLogger(__name__)

# Minimum chain length for compression rule.
_COMPRESSION_MIN_CHAIN_LEN = 5
# Min average generational gap (years). Below this — suspicious.
_COMPRESSION_MIN_AVG_GAP_YEARS = 15
# Cap для древнего "named figure" — anchors typically pre-1500.
_NAMED_FIGURE_MAX_BIRTH_YEAR = 1500


def _load_anchor_names() -> set[str]:
    """Загрузить normalized anchor names из YAML (lazy, cached)."""
    try:
        text = (
            resources.files("gedcom_parser.fantasy.data")
            .joinpath("known_fabrication_anchors.yaml")
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        _LOG.warning("anchor list not found: %s; rule will skip", exc)
        return set()
    # Минимальный YAML-парсер: одна строка = один anchor (без значений).
    # Сознательно НЕ зависим от PyYAML здесь (gedcom-parser package
    # держит deps узкими). Если позже понадобится — добавим.
    names: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip leading "- " если YAML-list форма.
        if line.startswith("- "):
            line = line[2:].strip()
        # Strip surrounding quotes.
        line = line.strip('"').strip("'")
        if line:
            names.add(line.lower())
    return names


class SuspiciousGenerationalCompressionRule:
    """≥5 поколений с avg gap <15 лет — high (likely missing intermediates)."""

    rule_id: str = "suspicious_generational_compression"
    default_severity: FantasySeverity = FantasySeverity.HIGH

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        # Build child→single-parent (mother preferred for stability) map.
        first_parent: dict[str, str] = {}
        for family in doc.families.values():
            primary = family.wife_xref or family.husband_xref
            if primary is None:
                continue
            for child_xref in family.children_xrefs:
                first_parent.setdefault(child_xref, primary)

        # Walk up from every leaf (person not anyone's recorded parent),
        # but for simplicity every person — каждая считает свою up-chain.
        # Эмитим только uniqueness'но (по setу xrefs).
        seen_chains: set[tuple[str, ...]] = set()
        for start_xref in doc.persons:
            chain = self._walk_up(start_xref, first_parent)
            if len(chain) < _COMPRESSION_MIN_CHAIN_LEN:
                continue
            chain_key = tuple(chain)
            if chain_key in seen_chains:
                continue
            seen_chains.add(chain_key)
            avg_gap = self._average_gap(doc, chain)
            if avg_gap is None or avg_gap >= _COMPRESSION_MIN_AVG_GAP_YEARS:
                continue
            yield FantasyFlag(
                rule_id=self.rule_id,
                severity=FantasySeverity.HIGH,
                confidence=0.75,
                reason=(
                    f"Direct ancestor chain of {len(chain)} generations from "
                    f"{chain[0]} to {chain[-1]} has average generational gap of "
                    f"{avg_gap:.1f} years (threshold {_COMPRESSION_MIN_AVG_GAP_YEARS})."
                ),
                person_xref=chain[0],
                evidence={
                    "chain_xrefs": list(chain),
                    "chain_length": len(chain),
                    "avg_gap_years": round(avg_gap, 2),
                    "min_gap_threshold": _COMPRESSION_MIN_AVG_GAP_YEARS,
                },
                suggested_action=(
                    "Likely missing intermediate generation(s). Verify each link "
                    "against primary records."
                ),
            )

    def _walk_up(self, start: str, first_parent: dict[str, str]) -> list[str]:
        """Linear walk up через single-parent chain; cycle-safe."""
        chain: list[str] = [start]
        seen: set[str] = {start}
        node = start
        while True:
            parent = first_parent.get(node)
            if parent is None or parent in seen:
                break
            chain.append(parent)
            seen.add(parent)
            node = parent
        return chain

    def _average_gap(self, doc: GedcomDocument, chain: list[str]) -> float | None:
        """Mean (parent_birth - child_birth) across links with both years."""
        gaps: list[int] = []
        for i in range(len(chain) - 1):
            child = doc.get_person(chain[i])
            parent = doc.get_person(chain[i + 1])
            cb = birth_year(child)
            pb = birth_year(parent)
            if cb is None or pb is None:
                continue
            if cb <= pb:
                # Defective link (covered by child_before_parent_birth) — skip.
                continue
            gaps.append(cb - pb)
        if not gaps:
            return None
        return sum(gaps) / len(gaps)


class DirectDescentFromPre1500NamedFigureRule:
    """Claim of direct descent from pre-1500 anchor name — HIGH if uncited.

    Heuristic: any person born ≤ 1500 чьё primary surname (или any name part)
    matches anchor list AND кто появляется как ancestor of модерн-deceased
    person without any source citations on intermediate nodes.

    Anti-drift: anchor list — это narrow whitelist of viral fabrication
    anchors (Charlemagne, Genghis Khan, …), не surname/ethnicity filter.
    """

    rule_id: str = "direct_descent_from_pre_1500_named_figure"
    default_severity: FantasySeverity = FantasySeverity.HIGH

    def __init__(self) -> None:
        self._anchors: set[str] = _load_anchor_names()

    def evaluate(
        self,
        doc: GedcomDocument,
        ctx: FantasyContext,  # noqa: ARG002 — Protocol API symmetry.
    ) -> Iterable[FantasyFlag]:
        if not self._anchors:
            return
        # Build child→parents map.
        parents_of: dict[str, set[str]] = defaultdict(set)
        for family in doc.families.values():
            ps: set[str] = set()
            if family.husband_xref:
                ps.add(family.husband_xref)
            if family.wife_xref:
                ps.add(family.wife_xref)
            for ch in family.children_xrefs:
                parents_of[ch].update(ps)

        # Find anchor candidates: persons born ≤ 1500 whose name matches anchor.
        anchor_xrefs: set[str] = set()
        for person in doc.persons.values():
            yr = birth_year(person)
            if yr is None or yr > _NAMED_FIGURE_MAX_BIRTH_YEAR:
                continue
            if self._person_matches_anchor(person):
                anchor_xrefs.add(person.xref_id)
        if not anchor_xrefs:
            return

        # For every modern person (born ≥ 1800 для безопасности), walk up
        # ancestors. If we reach an anchor AND no citation на пути — flag.
        for descendant in doc.persons.values():
            d_birth = birth_year(descendant)
            if d_birth is None or d_birth < 1800:
                continue
            chain_to_anchor = self._walk_to_anchor(descendant.xref_id, parents_of, anchor_xrefs)
            if chain_to_anchor is None:
                continue
            if self._chain_has_any_citation(doc, chain_to_anchor):
                continue
            anchor_xref = chain_to_anchor[-1]
            yield FantasyFlag(
                rule_id=self.rule_id,
                severity=FantasySeverity.HIGH,
                confidence=0.8,
                reason=(
                    f"Person {descendant.xref_id} (born {d_birth}) traces direct "
                    f"descent to {anchor_xref}, a known viral-fabrication anchor "
                    "(pre-1500 historical figure). Chain has zero source citations."
                ),
                person_xref=descendant.xref_id,
                evidence={
                    "descendant_xref": descendant.xref_id,
                    "anchor_xref": anchor_xref,
                    "chain_xrefs": chain_to_anchor,
                    "chain_length": len(chain_to_anchor),
                },
                suggested_action=(
                    "Verify each generational link with primary sources. Direct "
                    "descent from pre-1500 named figures is rare and almost always "
                    "requires intermediate gaps that fabricators paper over."
                ),
            )

    def _person_matches_anchor(self, person: Person) -> bool:
        for name in person.names:
            full = f"{name.given or ''} {name.surname or ''}".strip().lower()
            if full and full in self._anchors:
                return True
            # Also surname-only match (Charlemagne / Карл Великий exists as one-name).
            if name.surname and name.surname.lower() in self._anchors:
                return True
            if name.given and name.given.lower() in self._anchors:
                return True
        return False

    def _walk_to_anchor(
        self,
        start: str,
        parents_of: dict[str, set[str]],
        anchor_xrefs: set[str],
    ) -> list[str] | None:
        """BFS до anchor; вернуть xref-chain (start → anchor) или None."""
        prev: dict[str, str] = {}
        seen: set[str] = {start}
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in anchor_xrefs and node != start:
                # Reconstruct chain.
                chain = [node]
                while chain[-1] in prev:
                    chain.append(prev[chain[-1]])
                chain.reverse()
                return chain
            for parent in parents_of.get(node, ()):
                if parent not in seen:
                    seen.add(parent)
                    prev[parent] = node
                    queue.append(parent)
        return None

    def _chain_has_any_citation(self, doc: GedcomDocument, chain: list[str]) -> bool:
        for xref in chain:
            p = doc.get_person(xref)
            if p is not None and (p.citations or p.sources_xrefs):
                return True
        return False


__all__ = [
    "DirectDescentFromPre1500NamedFigureRule",
    "SuspiciousGenerationalCompressionRule",
]
