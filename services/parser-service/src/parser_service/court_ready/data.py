"""Data-collection слой Court-Ready Report.

Read-only функции, собирающие ``ReportContext`` из ORM. Никаких mutations.
Архитектура — широкий fan-out коллекторов:

* :func:`collect_subject_summary` — vital stats + AKA names.
* :func:`collect_events_for_person` — все Event'ы где persons — participant.
* :func:`collect_relationships_for_person` — родители + дети + супруги + сиблинги.
* :func:`collect_ancestry_lines` — рекурсивный обход parent_child до ``target_gen``.
* :func:`derive_negative_findings` — events без citations, relationships без evidence.

Output в одном проходе склеивается :func:`build_report_context`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable

from shared_models.orm import (
    Citation,
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Hypothesis,
    Name,
    Person,
    Place,
    Source,
    Tree,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.court_ready.locale import t
from parser_service.court_ready.models import (
    CitationRef,
    EventClaim,
    NegativeFinding,
    RelationshipClaim,
    ReportContext,
    ReportLocale,
    ReportScope,
    SubjectSummary,
)

# GEDCOM event types, считающиеся «vital» — отсутствие любого из них на
# subject'е попадает в Negative findings как missing_vital.
_VITAL_EVENTS: frozenset[str] = frozenset({"BIRT", "DEAT"})

# Marriage-родственные event_types — Citation на event с этим типом
# принимается как evidence для spouse-relationship.
_SPOUSE_EVENT_TYPES: frozenset[str] = frozenset({"MARR", "DIV", "ENGA", "ANUL", "MARC", "MARS"})


# ---------------------------------------------------------------------------
# Person + name helpers
# ---------------------------------------------------------------------------


async def _load_person(session: AsyncSession, person_id: uuid.UUID) -> Person | None:
    res = await session.execute(
        select(Person).where(Person.id == person_id, Person.deleted_at.is_(None))
    )
    return res.scalar_one_or_none()


async def _load_tree(session: AsyncSession, tree_id: uuid.UUID) -> Tree | None:
    res = await session.execute(select(Tree).where(Tree.id == tree_id))
    return res.scalar_one_or_none()


async def _load_names(session: AsyncSession, person_id: uuid.UUID) -> list[Name]:
    res = await session.execute(
        select(Name)
        .where(Name.person_id == person_id, Name.deleted_at.is_(None))
        .order_by(Name.sort_order, Name.created_at)
    )
    return list(res.scalars().all())


def _format_name(name: Name) -> str:
    """Имя одной строкой; пустые поля пропускает."""
    parts = [
        (name.prefix or "").strip(),
        (name.given_name or "").strip(),
        (name.patronymic or "").strip(),
        (name.surname or "").strip(),
        (name.suffix or "").strip(),
    ]
    rendered = " ".join(p for p in parts if p)
    return rendered or "(unnamed)"


def _primary_name(names: list[Name]) -> str:
    if not names:
        return "(unnamed)"
    return _format_name(names[0])


def _aka_names(names: list[Name]) -> list[str]:
    """Все name-rows кроме первого — как AKA-варианты, дедуп-нутые по строке."""
    seen: set[str] = set()
    out: list[str] = []
    for n in names[1:]:
        rendered = _format_name(n)
        if rendered and rendered not in seen and rendered != "(unnamed)":
            seen.add(rendered)
            out.append(rendered)
    return out


# ---------------------------------------------------------------------------
# Citations / sources
# ---------------------------------------------------------------------------


async def _citations_for_entity(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
) -> list[tuple[Citation, Source]]:
    """Все (Citation, Source) для одной сущности. Soft-deleted исключены."""
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == entity_type,
            Citation.entity_id == entity_id,
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
        .order_by(Citation.created_at)
    )
    return [(c, s) for c, s in res.all()]


def _to_citation_ref(citation: Citation, source: Source) -> CitationRef:
    return CitationRef(
        source_id=source.id,
        citation_id=citation.id,
        source_title=source.title,
        author=source.author,
        publication=source.publication,
        publication_date=source.publication_date,
        repository=source.repository,
        url=source.url,
        page_or_section=citation.page_or_section,
        quoted_text=citation.quoted_text,
        quality=float(citation.quality),
        quay_raw=citation.quay_raw,
    )


# ---------------------------------------------------------------------------
# Subject summary + events
# ---------------------------------------------------------------------------


async def collect_subject_summary(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
) -> tuple[SubjectSummary, list[EventClaim]]:
    """Vital-events (BIRT/DEAT) + полный список event'ов персоны.

    Возвращает (summary, all_events). ``summary.birth`` / ``summary.death``
    — копии event'ов из ``all_events``; рендер их различает по позиции.
    """
    person = await _load_person(session, person_id)
    if person is None:
        msg = f"Person {person_id} not found"
        raise KeyError(msg)

    names = await _load_names(session, person_id)
    primary = _primary_name(names)
    aka = _aka_names(names)

    events = await _events_for_person(session, person_id=person_id)
    birth = next((e for e in events if e.event_type == "BIRT"), None)
    death = next((e for e in events if e.event_type == "DEAT"), None)

    return (
        SubjectSummary(
            person_id=person.id,
            primary_name=primary,
            aka_names=aka,
            sex=person.sex,
            birth=birth,
            death=death,
        ),
        events,
    )


async def _events_for_person(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
) -> list[EventClaim]:
    """EventParticipant → Event → EventClaim, с place + citations."""
    rows = await session.execute(
        select(Event, Place)
        .select_from(EventParticipant)
        .join(Event, Event.id == EventParticipant.event_id)
        .outerjoin(Place, Place.id == Event.place_id)
        .where(
            EventParticipant.person_id == person_id,
            Event.deleted_at.is_(None),
        )
        .order_by(Event.date_start.nullslast(), Event.created_at)
    )
    out: list[EventClaim] = []
    for event, place in rows.all():
        cits = await _citations_for_entity(session, entity_type="event", entity_id=event.id)
        out.append(
            EventClaim(
                event_id=event.id,
                event_type=event.event_type,
                custom_type=event.custom_type,
                date_raw=event.date_raw,
                date_start=event.date_start,
                date_end=event.date_end,
                place_name=place.canonical_name if place is not None else None,
                description=event.description,
                citations=[_to_citation_ref(c, s) for c, s in cits],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


async def collect_relationships_for_person(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    tree_id: uuid.UUID,
) -> list[RelationshipClaim]:
    """Все 4 типа relationships: parent / child / spouse / sibling.

    Confidence:

    * Если у пары есть Hypothesis(direction=match) того же типа →
      ``bayesian_fusion_v2`` + composite_score.
    * Иначе если есть citation на family / spouse-event → ``naive_count`` + 1.0.
    * Иначе ``asserted_only`` + 0.0 — связь существует в схеме, но evidence нет.
    """
    out: list[RelationshipClaim] = []

    parent_families = await _families_where_person_is_child(session, person_id=person_id)
    for fam in parent_families:
        for parent_id in (fam.husband_id, fam.wife_id):
            if parent_id is None:
                continue
            claim = await _build_relationship_claim(
                session,
                tree_id=tree_id,
                kind="parent",
                hypothesis_type="parent_child",
                self_id=person_id,
                other_id=parent_id,
                family=fam,
            )
            if claim is not None:
                out.append(claim)

    spouse_families = await _families_where_person_is_spouse(session, person_id=person_id)
    for fam in spouse_families:
        # Children
        children_rows = await session.execute(
            select(FamilyChild).where(FamilyChild.family_id == fam.id)
        )
        for fc in children_rows.scalars().all():
            claim = await _build_relationship_claim(
                session,
                tree_id=tree_id,
                kind="child",
                hypothesis_type="parent_child",
                self_id=person_id,
                other_id=fc.child_person_id,
                family=fam,
            )
            if claim is not None:
                out.append(claim)
        # Spouse — другой principal этой family.
        partner_id = fam.wife_id if fam.husband_id == person_id else fam.husband_id
        if partner_id is not None:
            claim = await _build_relationship_claim(
                session,
                tree_id=tree_id,
                kind="spouse",
                hypothesis_type="marriage",
                self_id=person_id,
                other_id=partner_id,
                family=fam,
                include_spouse_event_citations=True,
            )
            if claim is not None:
                out.append(claim)

    siblings = await _sibling_ids(session, person_id=person_id)
    for sib_id in siblings:
        sib_family = await _shared_family_of_children(session, person_a=person_id, person_b=sib_id)
        claim = await _build_relationship_claim(
            session,
            tree_id=tree_id,
            kind="sibling",
            hypothesis_type="siblings",
            self_id=person_id,
            other_id=sib_id,
            family=sib_family,
        )
        if claim is not None:
            out.append(claim)

    return out


async def _families_where_person_is_child(
    session: AsyncSession, *, person_id: uuid.UUID
) -> list[Family]:
    res = await session.execute(
        select(Family)
        .join(FamilyChild, FamilyChild.family_id == Family.id)
        .where(
            FamilyChild.child_person_id == person_id,
            Family.deleted_at.is_(None),
        )
    )
    return list(res.scalars().all())


async def _families_where_person_is_spouse(
    session: AsyncSession, *, person_id: uuid.UUID
) -> list[Family]:
    res = await session.execute(
        select(Family).where(
            or_(Family.husband_id == person_id, Family.wife_id == person_id),
            Family.deleted_at.is_(None),
        )
    )
    return list(res.scalars().all())


async def _sibling_ids(session: AsyncSession, *, person_id: uuid.UUID) -> list[uuid.UUID]:
    """Все persons, делящие хотя бы одну Family как FamilyChild."""
    fam_subq = (
        select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_id)
    ).subquery()
    res = await session.execute(
        select(FamilyChild.child_person_id)
        .where(
            FamilyChild.family_id.in_(select(fam_subq)),
            FamilyChild.child_person_id != person_id,
        )
        .distinct()
    )
    return [row[0] for row in res.all()]


async def _shared_family_of_children(
    session: AsyncSession, *, person_a: uuid.UUID, person_b: uuid.UUID
) -> Family | None:
    """Первая Family, в которой оба persons — children."""
    a_subq = select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_a)
    b_subq = select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_b)
    res = await session.execute(
        select(Family).where(
            Family.id.in_(a_subq),
            Family.id.in_(b_subq),
            Family.deleted_at.is_(None),
        )
    )
    return res.scalar_one_or_none()


async def _build_relationship_claim(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    kind: str,
    hypothesis_type: str,
    self_id: uuid.UUID,
    other_id: uuid.UUID,
    family: Family | None,
    include_spouse_event_citations: bool = False,
) -> RelationshipClaim | None:
    """Resolve evidence для одной relationship-pair и собрать DTO.

    ``family=None`` для sibling если по какой-то причине shared-family не
    нашлась — claim тогда ``asserted_only`` без citations (защитное
    программирование от inconsistent state).
    """
    other_names = await _load_names(session, other_id)
    other_name = _primary_name(other_names)

    citations: list[CitationRef] = []
    if family is not None:
        fam_cits = await _citations_for_entity(session, entity_type="family", entity_id=family.id)
        citations.extend(_to_citation_ref(c, s) for c, s in fam_cits)
        if include_spouse_event_citations:
            spouse_event_cits = await _spouse_event_citations(session, family_id=family.id)
            citations.extend(_to_citation_ref(c, s) for c, s in spouse_event_cits)

    hypothesis = await _find_hypothesis(
        session,
        tree_id=tree_id,
        hypothesis_type=hypothesis_type,
        person_a=self_id,
        person_b=other_id,
    )

    if hypothesis is not None:
        return RelationshipClaim(
            relation_kind=kind,
            other_person_id=other_id,
            other_person_name=other_name,
            evidence_type="inference_rule" if not citations else "citation",
            confidence_score=float(hypothesis.composite_score),
            confidence_method="bayesian_fusion_v2",
            citations=citations,
        )
    if citations:
        return RelationshipClaim(
            relation_kind=kind,
            other_person_id=other_id,
            other_person_name=other_name,
            evidence_type="citation",
            confidence_score=1.0,
            confidence_method="naive_count",
            citations=citations,
        )
    return RelationshipClaim(
        relation_kind=kind,
        other_person_id=other_id,
        other_person_name=other_name,
        evidence_type="asserted_only",
        confidence_score=0.0,
        confidence_method="asserted_only",
        citations=[],
    )


async def _spouse_event_citations(
    session: AsyncSession, *, family_id: uuid.UUID
) -> list[tuple[Citation, Source]]:
    """Citations on MARR/DIV events чьи participants — данная family."""
    event_ids_subq = (
        select(Event.id)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(
            EventParticipant.family_id == family_id,
            Event.event_type.in_(_SPOUSE_EVENT_TYPES),
            Event.deleted_at.is_(None),
        )
    ).subquery()
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == "event",
            Citation.entity_id.in_(select(event_ids_subq)),
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
    )
    return [(c, s) for c, s in res.all()]


async def _find_hypothesis(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    hypothesis_type: str,
    person_a: uuid.UUID,
    person_b: uuid.UUID,
) -> Hypothesis | None:
    res = await session.execute(
        select(Hypothesis).where(
            Hypothesis.tree_id == tree_id,
            Hypothesis.hypothesis_type == hypothesis_type,
            Hypothesis.deleted_at.is_(None),
            or_(
                (Hypothesis.subject_a_id == person_a) & (Hypothesis.subject_b_id == person_b),
                (Hypothesis.subject_a_id == person_b) & (Hypothesis.subject_b_id == person_a),
            ),
        )
    )
    return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Ancestry
# ---------------------------------------------------------------------------


async def collect_ancestry_lines(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    target_gen: int,
) -> list[SubjectSummary]:
    """BFS вверх по parent_child от subject'а до ``target_gen``-го поколения.

    Возвращает summaries предков (без subject'а и без other_events).
    Цикл-protection через visited-set, защищает от inconsistent данных.
    """
    if target_gen < 1:
        return []

    out: list[SubjectSummary] = []
    visited: set[uuid.UUID] = {person_id}
    frontier: set[uuid.UUID] = {person_id}

    for _gen in range(target_gen):
        next_frontier: set[uuid.UUID] = set()
        for pid in frontier:
            parent_ids = await _parents_of(session, person_id=pid)
            for parent_id in parent_ids:
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                next_frontier.add(parent_id)
                summary, _events = await collect_subject_summary(session, person_id=parent_id)
                out.append(summary)
        frontier = next_frontier
        if not frontier:
            break
    return out


async def _parents_of(session: AsyncSession, *, person_id: uuid.UUID) -> list[uuid.UUID]:
    res = await session.execute(
        select(Family.husband_id, Family.wife_id)
        .join(FamilyChild, FamilyChild.family_id == Family.id)
        .where(FamilyChild.child_person_id == person_id, Family.deleted_at.is_(None))
    )
    out: list[uuid.UUID] = []
    for husband_id, wife_id in res.all():
        if husband_id is not None:
            out.append(husband_id)
        if wife_id is not None:
            out.append(wife_id)
    return out


# ---------------------------------------------------------------------------
# Negative findings
# ---------------------------------------------------------------------------


def derive_negative_findings(
    *,
    subject: SubjectSummary,
    events: Iterable[EventClaim],
    relationships: Iterable[RelationshipClaim],
) -> list[NegativeFinding]:
    """Build Negative findings list:

    * Event без citations → ``event_without_source``.
    * Relationship с ``evidence_type='asserted_only'`` → ``relationship_without_evidence``.
    * Отсутствие BIRT / DEAT на subject'е → ``missing_vital``.
    """
    findings: list[NegativeFinding] = []

    for event in events:
        if not event.citations:
            findings.append(
                NegativeFinding(
                    kind="event_without_source",
                    description=f"{event.event_type}{f' ({event.custom_type})' if event.custom_type else ''}",
                    related_event_id=event.event_id,
                    related_person_id=subject.person_id,
                )
            )
    for rel in relationships:
        if rel.evidence_type == "asserted_only":
            findings.append(
                NegativeFinding(
                    kind="relationship_without_evidence",
                    description=f"{rel.relation_kind} → {rel.other_person_name}",
                    related_person_id=rel.other_person_id,
                )
            )

    if subject.birth is None:
        findings.append(
            NegativeFinding(kind="missing_vital", description="BIRT (no event recorded)")
        )
    if subject.death is None:
        findings.append(
            NegativeFinding(kind="missing_vital", description="DEAT (no event recorded)")
        )

    return findings


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


async def build_report_context(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    scope: ReportScope,
    target_gen: int | None,
    locale: ReportLocale,
    researcher_name: str | None = None,
) -> ReportContext:
    """Assemble всё в ``ReportContext``.

    Raises:
        KeyError: если ``person_id`` не существует.
    """
    person = await _load_person(session, person_id)
    if person is None:
        msg = f"Person {person_id} not found"
        raise KeyError(msg)

    tree = await _load_tree(session, person.tree_id)
    tree_name = tree.name if tree is not None else "(unknown)"

    subject, events = await collect_subject_summary(session, person_id=person_id)
    other_events = [
        e for e in events if e.event_type not in _VITAL_EVENTS or e.event_type == "CUSTOM"
    ]

    relationships: list[RelationshipClaim] = []
    if scope in ("person", "family", "ancestry_to_gen"):
        relationships = await collect_relationships_for_person(
            session, person_id=person_id, tree_id=person.tree_id
        )

    ancestry: list[SubjectSummary] = []
    if scope == "ancestry_to_gen" and target_gen is not None:
        ancestry = await collect_ancestry_lines(session, person_id=person_id, target_gen=target_gen)

    negative = derive_negative_findings(
        subject=subject,
        events=events,
        relationships=relationships,
    )

    return ReportContext(
        report_id=uuid.uuid4(),
        generated_at=dt.datetime.now(dt.UTC),
        locale=locale,
        scope=scope,
        target_gen=target_gen,
        tree_id=person.tree_id,
        tree_name=tree_name,
        subject=subject,
        other_events=other_events,
        relationships=relationships,
        ancestry=ancestry,
        negative_findings=negative,
        methodology_statement=t("default_methodology", locale),
        researcher_name=researcher_name,
    )


__all__ = [
    "build_report_context",
    "collect_ancestry_lines",
    "collect_relationships_for_person",
    "collect_subject_summary",
    "derive_negative_findings",
]
