"""DB-backed fantasy scan executor (Phase 5.10).

Загружает persons + families + birth/death events из БД, строит
in-memory ``TreeView``, который quack-typed как :class:`GedcomDocument`
для целей `gedcom_parser.fantasy` правил, и персистит результирующие
flags в ``fantasy_flags``.

**No GEDCOM re-parse.** Scan работает над уже-импортированной DB-version
дерева. Это означает: rules видят персон / семьи как они есть в БД
после import + manual edits + merges.

**Scan-by-default replace:** новый scan дропает все active (не-dismissed)
flags для tree_id перед INSERT'ом — иначе старые stale-flags копились
бы. Dismissed flags сохраняются (user-decision retention).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from gedcom_parser.fantasy import scan_document
from gedcom_parser.fantasy.types import FantasyContext, FantasyFlag
from shared_models.orm import (
    Event,
    EventParticipant,
    Family,
    FamilyChild,
)
from shared_models.orm import (
    FantasyFlag as FantasyFlagOrm,
)
from shared_models.orm import (
    Person as PersonOrm,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from collections.abc import Iterable

_LOG = logging.getLogger(__name__)


# ── Lightweight TreeView adapter ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Date:
    """Подобие ``gedcom_parser.dates.ParsedDate`` для year-precision rules."""

    date_lower: Any  # имеет .year атрибут (datetime.date)
    date_upper: Any | None = None


@dataclass(frozen=True, slots=True)
class _Event:
    """Подобие ``gedcom_parser.entities.Event`` (только tag + year)."""

    tag: str
    date: _Date | None


@dataclass(frozen=True, slots=True)
class _Name:
    """Подобие ``gedcom_parser.entities.Name`` (given + surname)."""

    given: str | None = None
    surname: str | None = None


@dataclass(frozen=True, slots=True)
class _PersonView:
    """Quack-typed как ``gedcom_parser.entities.Person``."""

    xref_id: str
    db_id: uuid.UUID
    names: tuple[_Name, ...] = ()
    events: tuple[_Event, ...] = ()
    citations: tuple[Any, ...] = ()
    sources_xrefs: tuple[str, ...] = ()
    families_as_spouse: tuple[str, ...] = ()
    families_as_child: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FamilyView:
    """Quack-typed как ``gedcom_parser.entities.Family``."""

    xref_id: str
    db_id: uuid.UUID
    husband_xref: str | None = None
    wife_xref: str | None = None
    children_xrefs: tuple[str, ...] = ()


@dataclass
class TreeView:
    """In-memory snapshot дерева для запуска fantasy rules.

    ``persons`` / ``families`` keyed by synthetic xref (либо ``gedcom_xref``
    если задан, либо ``f"DB:{uuid}"`` для DB-only записей).
    """

    persons: dict[str, _PersonView] = field(default_factory=dict)
    families: dict[str, _FamilyView] = field(default_factory=dict)
    # xref → DB UUID, для resolve flag.subject_person_id после scan'а.
    xref_to_person_id: dict[str, uuid.UUID] = field(default_factory=dict)
    xref_to_family_id: dict[str, uuid.UUID] = field(default_factory=dict)

    def get_person(self, xref: str | None) -> _PersonView | None:
        if xref is None:
            return None
        return self.persons.get(xref)


# ── Scan summary DTO ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ScanSummary:
    """Возвращается из :func:`execute_fantasy_scan` (для API-ответа)."""

    scan_id: uuid.UUID
    tree_id: uuid.UUID
    persons_scanned: int
    families_scanned: int
    flags_created: int
    flags_replaced: int
    by_severity: dict[str, int]


# ── Loader ───────────────────────────────────────────────────────────────────


async def load_tree_view(session: AsyncSession, tree_id: uuid.UUID) -> TreeView:
    """Прочитать дерево из БД в синтетический TreeView.

    Single-pass подход: 4 query (persons, families, family_children, events
    через event_participants). Для 50k-person дерева — порядок MB-of-rows,
    приемлемо для intermittent admin-сканов.
    """
    view = TreeView()

    # Persons.
    person_rows = (
        (
            await session.execute(
                select(PersonOrm).where(
                    PersonOrm.tree_id == tree_id,
                    PersonOrm.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    person_id_to_xref: dict[uuid.UUID, str] = {}
    for p in person_rows:
        xref = p.gedcom_xref or f"DB:{p.id}"
        person_id_to_xref[p.id] = xref
        view.xref_to_person_id[xref] = p.id

    # Families.
    family_rows = (
        (
            await session.execute(
                select(Family).where(
                    Family.tree_id == tree_id,
                    Family.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    family_id_to_xref: dict[uuid.UUID, str] = {}
    for f in family_rows:
        xref = f.gedcom_xref or f"DB:{f.id}"
        family_id_to_xref[f.id] = xref
        view.xref_to_family_id[xref] = f.id

    # Children edges.
    child_edges = (
        await session.execute(
            select(FamilyChild.family_id, FamilyChild.child_person_id).where(
                FamilyChild.family_id.in_([f.id for f in family_rows] or [uuid.uuid4()])
            )
        )
    ).all()
    family_children: dict[uuid.UUID, list[str]] = {}
    for fid, child_pid in child_edges:
        if child_pid in person_id_to_xref:
            family_children.setdefault(fid, []).append(person_id_to_xref[child_pid])

    # Person → families relationships (FAMS / FAMC).
    families_as_spouse: dict[str, list[str]] = {}
    families_as_child: dict[str, list[str]] = {}
    for f in family_rows:
        f_xref = family_id_to_xref[f.id]
        if f.husband_id is not None and f.husband_id in person_id_to_xref:
            families_as_spouse.setdefault(person_id_to_xref[f.husband_id], []).append(f_xref)
        if f.wife_id is not None and f.wife_id in person_id_to_xref:
            families_as_spouse.setdefault(person_id_to_xref[f.wife_id], []).append(f_xref)
        for child_xref in family_children.get(f.id, ()):
            families_as_child.setdefault(child_xref, []).append(f_xref)

    # Birth + Death events per person (via event_participants).
    events_by_person: dict[uuid.UUID, list[_Event]] = {}
    if person_rows:
        # Узкий filter: только BIRT / DEAT, остальные правилам не нужны.
        event_rows = (
            await session.execute(
                select(
                    EventParticipant.person_id,
                    Event.event_type,
                    Event.date_start,
                )
                .join(Event, Event.id == EventParticipant.event_id)
                .where(
                    EventParticipant.person_id.in_(list(person_id_to_xref.keys())),
                    Event.event_type.in_(["BIRT", "DEAT"]),
                    Event.deleted_at.is_(None),
                )
            )
        ).all()
        for pid, etype, dstart in event_rows:
            ev = _Event(
                tag=etype,
                date=_Date(date_lower=dstart, date_upper=dstart) if dstart is not None else None,
            )
            events_by_person.setdefault(pid, []).append(ev)

    # Materialise PersonViews.
    for p in person_rows:
        xref = person_id_to_xref[p.id]
        view.persons[xref] = _PersonView(
            xref_id=xref,
            db_id=p.id,
            names=(),  # Names в отдельной таблице; rules используют только anchor-rule
            events=tuple(events_by_person.get(p.id, [])),
            families_as_spouse=tuple(families_as_spouse.get(xref, [])),
            families_as_child=tuple(families_as_child.get(xref, [])),
        )

    # Materialise FamilyViews.
    for f in family_rows:
        xref = family_id_to_xref[f.id]
        view.families[xref] = _FamilyView(
            xref_id=xref,
            db_id=f.id,
            husband_xref=person_id_to_xref.get(f.husband_id) if f.husband_id else None,
            wife_xref=person_id_to_xref.get(f.wife_id) if f.wife_id else None,
            children_xrefs=tuple(family_children.get(f.id, [])),
        )

    return view


# ── Persistence ──────────────────────────────────────────────────────────────


async def _replace_active_flags(
    session: AsyncSession,
    tree_id: uuid.UUID,
    flags: Iterable[FantasyFlag],
    view: TreeView,
) -> tuple[int, int]:
    """Удалить active-flags для tree_id, INSERT'нуть новые. Returns (created, replaced)."""
    # Подсчёт удалённых для summary.
    replaced_q = await session.execute(
        select(FantasyFlagOrm.id).where(
            FantasyFlagOrm.tree_id == tree_id,
            FantasyFlagOrm.dismissed_at.is_(None),
        )
    )
    replaced_count = len(replaced_q.scalars().all())

    await session.execute(
        delete(FantasyFlagOrm).where(
            FantasyFlagOrm.tree_id == tree_id,
            FantasyFlagOrm.dismissed_at.is_(None),
        )
    )

    rows: list[FantasyFlagOrm] = []
    for f in flags:
        subject_person_id: uuid.UUID | None = None
        if f.person_xref is not None:
            subject_person_id = view.xref_to_person_id.get(f.person_xref)
        # Family-level subject pointer — не materialised, кладём в evidence.
        evidence: dict[str, Any] = dict(f.evidence)
        if f.family_xref is not None:
            evidence["family_xref"] = f.family_xref
            family_db_id = view.xref_to_family_id.get(f.family_xref)
            if family_db_id is not None:
                evidence["family_db_id"] = str(family_db_id)
        if f.suggested_action is not None:
            evidence["suggested_action"] = f.suggested_action

        rows.append(
            FantasyFlagOrm(
                tree_id=tree_id,
                subject_person_id=subject_person_id,
                # subject_relationship_id не материализуем в v1 — relationship-id
                # как concept в БД нет, держим только family_xref в evidence.
                subject_relationship_id=None,
                rule_id=f.rule_id,
                severity=f.severity.value,
                confidence=f.confidence,
                reason=f.reason,
                evidence_json=evidence,
            )
        )
    if rows:
        session.add_all(rows)
        await session.flush()
    return len(rows), replaced_count


# ── Public API ───────────────────────────────────────────────────────────────


async def execute_fantasy_scan(
    session: AsyncSession,
    tree_id: uuid.UUID,
    *,
    enabled_rules: frozenset[str] | None = None,
) -> ScanSummary:
    """Полный round-trip scan: load → run rules → replace flags → summary.

    Не коммитит — caller отвечает за ``await session.commit()``.

    Args:
        session: открытая async-session.
        tree_id: UUID дерева.
        enabled_rules: Опциональный whitelist rule_id'ов; None = все default.

    Returns:
        :class:`ScanSummary` с агрегатами по severity.
    """
    view = await load_tree_view(session, tree_id)
    ctx = FantasyContext(enabled_rules=enabled_rules)
    flags = scan_document(view, ctx=ctx)  # type: ignore[arg-type]
    created, replaced = await _replace_active_flags(session, tree_id, flags, view)

    by_severity: dict[str, int] = {}
    for f in flags:
        by_severity[f.severity.value] = by_severity.get(f.severity.value, 0) + 1

    return ScanSummary(
        scan_id=uuid.uuid4(),
        tree_id=tree_id,
        persons_scanned=len(view.persons),
        families_scanned=len(view.families),
        flags_created=created,
        flags_replaced=replaced,
        by_severity=by_severity,
    )


__all__ = [
    "ScanSummary",
    "TreeView",
    "execute_fantasy_scan",
    "load_tree_view",
]
