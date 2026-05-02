"""Data-collection слой Relationship Research Report (Phase 24.3).

Read-only функции, собирающие ``RelationshipReportContext`` из ORM.
Никаких mutations, никаких новых ORM-table'ов — всё идёт против 15.x
evidence + 22.5 Evidence-rows + Hypothesis (Phase 8).

Точка входа — :func:`build_report_context`. Остальные функции — частные
коллекторы, экспортируются для unit-тестов.
"""

from __future__ import annotations

import datetime as dt
import uuid

from shared_models.orm import (
    Citation,
    DnaMatch,
    Event,
    EventParticipant,
    Evidence,
    Family,
    FamilyChild,
    Hypothesis,
    HypothesisEvidence,
    Name,
    Person,
    Source,
    Tree,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from report_service.relationship.locale import t
from report_service.relationship.models import (
    CitationRef,
    ClaimedRelationship,
    EvidencePiece,
    EvidenceSeverity,
    PersonSummary,
    ProvenanceSummary,
    RelationshipReportContext,
    ReportLocale,
    ReportTitleStyle,
    is_direct_claim,
)

# Marriage-родственные event_types — citation на event с этим типом
# принимается как evidence для spouse-claim'а. Зеркалит 15.1.
_SPOUSE_EVENT_TYPES: frozenset[str] = frozenset({"MARR", "DIV", "ENGA", "ANUL", "MARC", "MARS"})

# Phase 24.3 ClaimedRelationship → Phase 8 hypothesis_type. Только direct
# claim'ы маппятся 1:1; extended (cousin, grandparent, ...) гипотез не имеют.
_HYPOTHESIS_TYPE_FOR_CLAIM: dict[ClaimedRelationship, str] = {
    ClaimedRelationship.PARENT_CHILD: "parent_child",
    ClaimedRelationship.SIBLING: "siblings",
    ClaimedRelationship.SPOUSE: "marriage",
}


# ---------------------------------------------------------------------------
# Person + tree helpers
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
    seen: set[str] = set()
    out: list[str] = []
    for n in names[1:]:
        rendered = _format_name(n)
        if rendered and rendered not in seen and rendered != "(unnamed)":
            seen.add(rendered)
            out.append(rendered)
    return out


async def _vital_year(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
    event_type: str,
) -> int | None:
    """Year первого BIRT/DEAT event'а персоны. None если нет даты."""
    res = await session.execute(
        select(Event.date_start)
        .select_from(EventParticipant)
        .join(Event, Event.id == EventParticipant.event_id)
        .where(
            EventParticipant.person_id == person_id,
            Event.event_type == event_type,
            Event.deleted_at.is_(None),
        )
        .order_by(Event.date_start.nullslast(), Event.created_at)
        .limit(1)
    )
    row = res.first()
    if row is None or row[0] is None:
        return None
    return int(row[0].year)


async def _build_person_summary(
    session: AsyncSession,
    *,
    person_id: uuid.UUID,
) -> PersonSummary | None:
    person = await _load_person(session, person_id)
    if person is None:
        return None
    names = await _load_names(session, person_id)
    return PersonSummary(
        person_id=person.id,
        primary_name=_primary_name(names),
        aka_names=_aka_names(names),
        sex=person.sex,
        birth_year=await _vital_year(session, person_id=person_id, event_type="BIRT"),
        death_year=await _vital_year(session, person_id=person_id, event_type="DEAT"),
    )


# ---------------------------------------------------------------------------
# Direct-relationship resolvers (Family / FamilyChild)
# ---------------------------------------------------------------------------


async def _resolve_parent_child_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> list[Family]:
    """Ищем families где (A — родитель ∧ B — ребёнок) ИЛИ симметрично.

    Возвращает все matched Family rows; обычно это 0 или 1, но возможно
    несколько (foster + biological).
    """
    res = await session.execute(
        select(Family)
        .join(FamilyChild, FamilyChild.family_id == Family.id)
        .where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            or_(
                (or_(Family.husband_id == person_a_id, Family.wife_id == person_a_id))
                & (FamilyChild.child_person_id == person_b_id),
                (or_(Family.husband_id == person_b_id, Family.wife_id == person_b_id))
                & (FamilyChild.child_person_id == person_a_id),
            ),
        )
        .distinct()
    )
    return list(res.scalars().all())


async def _resolve_spouse_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> list[Family]:
    res = await session.execute(
        select(Family).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            or_(
                (Family.husband_id == person_a_id) & (Family.wife_id == person_b_id),
                (Family.husband_id == person_b_id) & (Family.wife_id == person_a_id),
            ),
        )
    )
    return list(res.scalars().all())


async def _resolve_sibling_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> list[Family]:
    a_subq = select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_a_id)
    b_subq = select(FamilyChild.family_id).where(FamilyChild.child_person_id == person_b_id)
    res = await session.execute(
        select(Family).where(
            Family.tree_id == tree_id,
            Family.deleted_at.is_(None),
            Family.id.in_(a_subq),
            Family.id.in_(b_subq),
        )
    )
    return list(res.scalars().all())


async def resolve_relationship_families(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
    claim: ClaimedRelationship,
) -> list[Family]:
    """Резолв direct-claim'ов (parent_child / sibling / spouse) на Family rows.

    Не-direct claims возвращают пустой список — для них Family-resolution
    не определена в Phase 24.3 v1.
    """
    if claim is ClaimedRelationship.PARENT_CHILD:
        return await _resolve_parent_child_families(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
        )
    if claim is ClaimedRelationship.SPOUSE:
        return await _resolve_spouse_families(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
        )
    if claim is ClaimedRelationship.SIBLING:
        return await _resolve_sibling_families(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
        )
    return []


# ---------------------------------------------------------------------------
# Citation aggregation
# ---------------------------------------------------------------------------


async def _citations_for_families(
    session: AsyncSession,
    *,
    family_ids: list[uuid.UUID],
) -> list[tuple[Citation, Source]]:
    if not family_ids:
        return []
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == "family",
            Citation.entity_id.in_(family_ids),
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
    )
    return [(c, s) for c, s in res.all()]


async def _spouse_event_citations(
    session: AsyncSession,
    *,
    family_ids: list[uuid.UUID],
) -> list[tuple[Citation, Source]]:
    if not family_ids:
        return []
    event_ids_subq = (
        select(Event.id)
        .join(EventParticipant, EventParticipant.event_id == Event.id)
        .where(
            EventParticipant.family_id.in_(family_ids),
            Event.event_type.in_(_SPOUSE_EVENT_TYPES),
            Event.deleted_at.is_(None),
        )
    )
    res = await session.execute(
        select(Citation, Source)
        .join(Source, Source.id == Citation.source_id)
        .where(
            Citation.entity_type == "event",
            Citation.entity_id.in_(event_ids_subq),
            Citation.deleted_at.is_(None),
            Source.deleted_at.is_(None),
        )
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


def _citation_to_evidence_piece(
    citation: Citation,
    source: Source,
    locale: ReportLocale,
) -> EvidencePiece:
    """Citation → supporting EvidencePiece. Weight = quality (0..1)."""
    ref = _to_citation_ref(citation, source)
    title = source.title or t("evidence_kind_citation", locale)
    return EvidencePiece(
        kind="citation",
        severity="supporting",
        title=title,
        description=citation.quoted_text or citation.page_or_section,
        weight=float(citation.quality),
        match_certainty=1.0,
        citations=[ref],
        provenance=None,
    )


# ---------------------------------------------------------------------------
# Hypothesis evidence
# ---------------------------------------------------------------------------


async def _find_hypothesis(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    hypothesis_type: str,
    subject_a_id: uuid.UUID,
    subject_b_id: uuid.UUID,
) -> Hypothesis | None:
    res = await session.execute(
        select(Hypothesis).where(
            Hypothesis.tree_id == tree_id,
            Hypothesis.hypothesis_type == hypothesis_type,
            Hypothesis.deleted_at.is_(None),
            or_(
                (Hypothesis.subject_a_id == subject_a_id)
                & (Hypothesis.subject_b_id == subject_b_id),
                (Hypothesis.subject_a_id == subject_b_id)
                & (Hypothesis.subject_b_id == subject_a_id),
            ),
        )
    )
    return res.scalar_one_or_none()


async def _hypothesis_evidences(
    session: AsyncSession,
    *,
    hypothesis_id: uuid.UUID,
) -> list[HypothesisEvidence]:
    res = await session.execute(
        select(HypothesisEvidence).where(HypothesisEvidence.hypothesis_id == hypothesis_id)
    )
    return list(res.scalars().all())


def _hypothesis_evidence_to_piece(ev: HypothesisEvidence) -> EvidencePiece:
    """HypothesisEvidence → EvidencePiece. Weight ∈ [0,1] (см. CHECK constraint)."""
    severity: EvidenceSeverity
    if ev.direction == "supports":
        severity = "supporting"
    elif ev.direction == "contradicts":
        severity = "contradicting"
    else:
        severity = "neutral"
    return EvidencePiece(
        kind="hypothesis_evidence",
        severity=severity,
        title=f"rule:{ev.rule_id}",
        description=ev.observation,
        weight=float(ev.weight),
        match_certainty=1.0,
        citations=[],
        provenance=None,
    )


# ---------------------------------------------------------------------------
# Off-catalog Evidence (Phase 22.5)
# ---------------------------------------------------------------------------


async def _off_catalog_evidence(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    family_ids: list[uuid.UUID],
    person_ids: list[uuid.UUID],
) -> list[Evidence]:
    """Off-catalog 22.5 Evidence-rows для given families + persons.

    Evidence — полиморфная (entity_type ∈ {family, person, event, ...}).
    Берём family + person attachments; event attachments игнорируем
    (они слишком общие и зашумят отчёт).
    """
    entity_filter = []
    if family_ids:
        entity_filter.append(
            (Evidence.entity_type == "family") & (Evidence.entity_id.in_(family_ids))
        )
    if person_ids:
        entity_filter.append(
            (Evidence.entity_type == "person") & (Evidence.entity_id.in_(person_ids))
        )
    if not entity_filter:
        return []
    res = await session.execute(
        select(Evidence).where(
            Evidence.tree_id == tree_id,
            Evidence.deleted_at.is_(None),
            or_(*entity_filter),
        )
    )
    return list(res.scalars().all())


def _provenance_summary(raw: dict[str, object]) -> ProvenanceSummary:
    """``Evidence.provenance`` JSONB → ProvenanceSummary DTO. Безопасный лоодер."""
    cost = raw.get("cost_usd")
    cost_f: float | None = None
    if cost is not None:
        try:
            cost_f = float(cost)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            cost_f = None
    return ProvenanceSummary(
        channel=str(raw.get("channel", "unknown")),
        cost_usd=cost_f,
        jurisdiction=_str_or_none(raw.get("jurisdiction")),
        archive_name=_str_or_none(raw.get("archive_name")),
        request_reference=_str_or_none(raw.get("request_reference")),
        notes=_str_or_none(raw.get("notes")),
        migrated=bool(raw.get("migrated", False)),
    )


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _evidence_to_piece(ev: Evidence) -> EvidencePiece:
    """22.5 Evidence-row → supporting EvidencePiece.

    Severity жёстко ``supporting`` — Phase 22.5 не moders contradicting
    evidence на этом уровне; contradicting приходит только из Hypothesis
    evidence.
    """
    return EvidencePiece(
        kind="off_catalog_evidence",
        severity="supporting",
        title=f"document_type={ev.document_type}",
        description=None,
        weight=float(ev.confidence) / max(float(ev.match_certainty), 1e-6),
        match_certainty=float(ev.match_certainty),
        citations=[],
        provenance=_provenance_summary(ev.provenance),
    )


# ---------------------------------------------------------------------------
# DNA evidence
# ---------------------------------------------------------------------------


async def _dna_pieces(
    session: AsyncSession,
    *,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
) -> list[EvidencePiece]:
    """Мин. DNA-evidence v1: любой DnaMatch row, где matched_person_id ∈ {A, B}.

    Без kit-owner резолва — Phase 24.4 расширит. Total cM (если есть)
    отображается в title; weight нормализуется как ``min(1.0, total_cm / 200)``
    (200 cM ~ 2nd cousin, грубая шкала).
    """
    res = await session.execute(
        select(DnaMatch).where(
            DnaMatch.deleted_at.is_(None),
            DnaMatch.matched_person_id.in_([person_a_id, person_b_id]),
        )
    )
    pieces: list[EvidencePiece] = []
    for match in res.scalars().all():
        weight = min(1.0, float(match.total_cm or 0) / 200.0)
        title = f"DNA match: {match.display_name or match.matched_person_id}"
        if match.total_cm is not None:
            title += f" ({match.total_cm:.1f} cM)"
        pieces.append(
            EvidencePiece(
                kind="dna_match",
                severity="supporting",
                title=title,
                description=match.predicted_relationship,
                weight=weight,
                match_certainty=0.8,  # cross-platform DNA matches are uncertain attachments
                citations=[],
                provenance=None,
            )
        )
    return pieces


# ---------------------------------------------------------------------------
# Methodology
# ---------------------------------------------------------------------------


def _methodology_statement(locale: ReportLocale) -> str:
    return t("default_methodology", locale)


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


async def build_report_context(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
    claim: ClaimedRelationship,
    locale: ReportLocale,
    title_style: ReportTitleStyle,
    include_dna_evidence: bool,
    include_archive_evidence: bool,
    include_hypothesis_flags: bool,
    researcher_name: str | None,
) -> RelationshipReportContext:
    """Собрать ``RelationshipReportContext`` из БД.

    Raises:
        KeyError: если одна из персон или дерево не найдены — caller'у
            мапить в HTTP 404.
    """
    tree = await _load_tree(session, tree_id)
    if tree is None:
        msg = f"Tree {tree_id} not found"
        raise KeyError(msg)

    person_a = await _build_person_summary(session, person_id=person_a_id)
    person_b = await _build_person_summary(session, person_id=person_b_id)
    if person_a is None or person_b is None:
        missing = person_a_id if person_a is None else person_b_id
        msg = f"Person {missing} not found in tree {tree_id}"
        raise KeyError(msg)

    families: list[Family] = []
    if is_direct_claim(claim):
        families = await resolve_relationship_families(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
            claim=claim,
        )
    direct_resolved = bool(families)
    family_ids = [f.id for f in families]

    supporting: list[EvidencePiece] = []
    contradicting: list[EvidencePiece] = []

    # Citations on family rows (direct evidence for parent_child / sibling / spouse).
    if include_archive_evidence:
        for cit, src in await _citations_for_families(session, family_ids=family_ids):
            supporting.append(_citation_to_evidence_piece(cit, src, locale))

    # Marriage-event citations (extra layer for spouse claim).
    if include_archive_evidence and claim is ClaimedRelationship.SPOUSE:
        for cit, src in await _spouse_event_citations(session, family_ids=family_ids):
            supporting.append(_citation_to_evidence_piece(cit, src, locale))

    # Hypothesis evidence.
    if include_hypothesis_flags and claim in _HYPOTHESIS_TYPE_FOR_CLAIM:
        hyp = await _find_hypothesis(
            session,
            tree_id=tree_id,
            hypothesis_type=_HYPOTHESIS_TYPE_FOR_CLAIM[claim],
            subject_a_id=person_a_id,
            subject_b_id=person_b_id,
        )
        if hyp is not None:
            for ev in await _hypothesis_evidences(session, hypothesis_id=hyp.id):
                piece = _hypothesis_evidence_to_piece(ev)
                if piece.severity == "supporting":
                    supporting.append(piece)
                elif piece.severity == "contradicting":
                    contradicting.append(piece)

    # Off-catalog Evidence (22.5).
    if include_archive_evidence:
        ev_rows = await _off_catalog_evidence(
            session,
            tree_id=tree_id,
            family_ids=family_ids,
            person_ids=[person_a_id, person_b_id],
        )
        for off_catalog_ev in ev_rows:
            supporting.append(_evidence_to_piece(off_catalog_ev))

    # DNA matches (best-effort v1).
    if include_dna_evidence:
        supporting.extend(
            await _dna_pieces(session, person_a_id=person_a_id, person_b_id=person_b_id)
        )

    # Compute confidence + method.
    from report_service.relationship.confidence import compute_confidence  # noqa: PLC0415

    confidence, method = compute_confidence(supporting, contradicting)

    # Build narrative — defer to dedicated module to keep determinism explicit.
    from report_service.relationship.narrative import build_narrative  # noqa: PLC0415

    narrative = build_narrative(
        person_a=person_a,
        person_b=person_b,
        claim=claim,
        evidence=supporting,
        counter_evidence=contradicting,
        direct_relationship_resolved=direct_resolved,
        locale=locale,
    )

    return RelationshipReportContext(
        report_id=uuid.uuid4(),
        generated_at=dt.datetime.now(dt.UTC),
        locale=locale,
        title_style=title_style,
        tree_id=tree_id,
        tree_name=tree.name,
        person_a=person_a,
        person_b=person_b,
        claimed_relationship=claim,
        is_direct_claim=is_direct_claim(claim),
        direct_relationship_resolved=direct_resolved,
        narrative=narrative,
        evidence=supporting,
        counter_evidence=contradicting,
        confidence=confidence,
        confidence_method=method,
        methodology_statement=_methodology_statement(locale),
        researcher_name=researcher_name,
    )


__all__ = [
    "build_report_context",
    "resolve_relationship_families",
]
