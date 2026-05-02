"""Unit-тесты narrative + confidence + render для Phase 24.3.

Не требуют БД — используют синтетический ``RelationshipReportContext``
сконструированный руками. Покрытие:

* Narrative детерминирован per claim_type.
* Confidence формула 22.5 (weight × match_certainty, supporting − contradicting).
* Counter-evidence section попадает в HTML только когда есть contradicting.
* HTML render идёт без StrictUndefined exceptions для пустых случаев.
* PDF render skipped если WeasyPrint native libs недоступны.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from report_service.relationship.confidence import compute_confidence
from report_service.relationship.models import (
    CitationRef,
    ClaimedRelationship,
    EvidencePiece,
    PersonSummary,
    ProvenanceSummary,
    RelationshipReportContext,
)
from report_service.relationship.narrative import build_narrative
from report_service.relationship.render import (
    PdfRenderError,
    render_html,
    render_pdf,
)

# ---------------------------------------------------------------------------
# Synthetic context builders
# ---------------------------------------------------------------------------


def _person(name: str, *, birth: int | None = None, death: int | None = None) -> PersonSummary:
    return PersonSummary(
        person_id=uuid.uuid4(),
        primary_name=name,
        aka_names=[],
        sex="U",
        birth_year=birth,
        death_year=death,
    )


def _citation_ref(title: str = "Birth registry, 1898") -> CitationRef:
    return CitationRef(
        source_id=uuid.uuid4(),
        citation_id=uuid.uuid4(),
        source_title=title,
        author=None,
        publication=None,
        publication_date=None,
        repository="State Archive #42",
        url=None,
        page_or_section="p. 12",
        quoted_text=None,
        quality=0.9,
        quay_raw=3,
    )


def _supporting_citation_piece() -> EvidencePiece:
    return EvidencePiece(
        kind="citation",
        severity="supporting",
        title="Birth registry",
        description="Direct citation linking parent to child.",
        weight=0.9,
        match_certainty=1.0,
        citations=[_citation_ref()],
        provenance=None,
    )


def _supporting_off_catalog_piece(*, weight: float = 3.0, certainty: float = 0.9) -> EvidencePiece:
    return EvidencePiece(
        kind="off_catalog_evidence",
        severity="supporting",
        title="document_type=birth_certificate",
        description=None,
        weight=weight,
        match_certainty=certainty,
        citations=[],
        provenance=ProvenanceSummary(
            channel="archive_visit",
            cost_usd=12.50,
            jurisdiction="UA",
            archive_name="State Archive of Kyiv Oblast",
            request_reference="R-2024-1101",
            notes=None,
            migrated=False,
        ),
    )


def _contradicting_hypothesis_piece() -> EvidencePiece:
    return EvidencePiece(
        kind="hypothesis_evidence",
        severity="contradicting",
        title="rule:age_window",
        description="Parent born after child — biologically impossible.",
        weight=0.95,
        match_certainty=1.0,
        citations=[],
        provenance=None,
    )


def _ctx(
    *,
    claim: ClaimedRelationship = ClaimedRelationship.PARENT_CHILD,
    evidence: list[EvidencePiece] | None = None,
    counter: list[EvidencePiece] | None = None,
    direct_resolved: bool = True,
) -> RelationshipReportContext:
    person_a = _person("Alice Doe", birth=1850, death=1920)
    person_b = _person("Bob Doe", birth=1875, death=1942)
    evidence = evidence if evidence is not None else [_supporting_citation_piece()]
    counter = counter if counter is not None else []
    confidence, method = compute_confidence(evidence, counter)
    narrative = build_narrative(
        person_a=person_a,
        person_b=person_b,
        claim=claim,
        evidence=evidence,
        counter_evidence=counter,
        direct_relationship_resolved=direct_resolved,
        locale="en",
    )
    return RelationshipReportContext(
        report_id=uuid.UUID("00000000-0000-0000-0000-000000000024"),
        generated_at=dt.datetime(2026, 5, 2, 10, 0, 0, tzinfo=dt.UTC),
        locale="en",
        title_style="formal",
        tree_id=uuid.uuid4(),
        tree_name="Test Tree",
        person_a=person_a,
        person_b=person_b,
        claimed_relationship=claim,
        is_direct_claim=claim
        in {
            ClaimedRelationship.PARENT_CHILD,
            ClaimedRelationship.SIBLING,
            ClaimedRelationship.SPOUSE,
        },
        direct_relationship_resolved=direct_resolved,
        narrative=narrative,
        evidence=evidence,
        counter_evidence=counter,
        confidence=confidence,
        confidence_method=method,
        methodology_statement="Test methodology.",
        researcher_name="Test Researcher",
    )


# ---------------------------------------------------------------------------
# Confidence formula
# ---------------------------------------------------------------------------


def test_confidence_pure_supporting_22_5() -> None:
    """One off-catalog tier-3 piece (weight=3) × certainty=0.9 → 2.7, method=bayesian_22_5."""
    piece = _supporting_off_catalog_piece(weight=3.0, certainty=0.9)
    score, method = compute_confidence([piece], [])
    assert score == pytest.approx(2.7)
    assert method == "bayesian_22_5"


def test_confidence_naive_when_only_citations() -> None:
    """Только citation-pieces → naive_count, score = sum(weight × certainty)."""
    a = EvidencePiece(
        kind="citation",
        severity="supporting",
        title="t1",
        weight=0.5,
        match_certainty=1.0,
        citations=[_citation_ref()],
    )
    b = EvidencePiece(
        kind="citation",
        severity="supporting",
        title="t2",
        weight=0.7,
        match_certainty=1.0,
        citations=[_citation_ref()],
    )
    score, method = compute_confidence([a, b], [])
    assert score == pytest.approx(1.2)
    assert method == "naive_count"


def test_confidence_subtracts_contradicting() -> None:
    """Contradicting > supporting → clamped к 0."""
    sup = _supporting_citation_piece()  # 0.9 × 1.0 = 0.9
    contra = _contradicting_hypothesis_piece()  # 0.95 × 1.0 = 0.95
    score, method = compute_confidence([sup], [contra])
    assert score == pytest.approx(0.0)
    assert method == "bayesian_22_5"


def test_confidence_asserted_only_when_no_evidence() -> None:
    score, method = compute_confidence([], [])
    assert score == 0.0
    assert method == "asserted_only"


# ---------------------------------------------------------------------------
# Narrative determinism
# ---------------------------------------------------------------------------


def test_narrative_deterministic_per_claim() -> None:
    """Тот же вход — побитово тот же narrative (snapshot-friendly)."""
    person_a = _person("Alice", birth=1850, death=1920)
    person_b = _person("Bob", birth=1875, death=1942)
    evidence = [_supporting_citation_piece()]
    n1 = build_narrative(
        person_a=person_a,
        person_b=person_b,
        claim=ClaimedRelationship.PARENT_CHILD,
        evidence=evidence,
        counter_evidence=[],
        direct_relationship_resolved=True,
        locale="en",
    )
    n2 = build_narrative(
        person_a=person_a,
        person_b=person_b,
        claim=ClaimedRelationship.PARENT_CHILD,
        evidence=evidence,
        counter_evidence=[],
        direct_relationship_resolved=True,
        locale="en",
    )
    assert n1 == n2
    # 6 paragraphs: intro + direct_resolution + summary + supporting_breakdown + closing
    # (counter_breakdown skipped because no contradicting evidence)
    assert n1.count("\n\n") == 4


def test_narrative_extended_claim_includes_caveat() -> None:
    n = build_narrative(
        person_a=_person("A"),
        person_b=_person("B"),
        claim=ClaimedRelationship.SECOND_COUSIN,
        evidence=[],
        counter_evidence=[],
        direct_relationship_resolved=False,
        locale="en",
    )
    assert "extended-distance" in n
    assert "second cousins" in n


def test_narrative_unresolved_direct_claim_warns() -> None:
    n = build_narrative(
        person_a=_person("A"),
        person_b=_person("B"),
        claim=ClaimedRelationship.PARENT_CHILD,
        evidence=[],
        counter_evidence=[],
        direct_relationship_resolved=False,
        locale="en",
    )
    assert "NOT present" in n


def test_narrative_ru_locale() -> None:
    n = build_narrative(
        person_a=_person("Алиса"),
        person_b=_person("Боб"),
        claim=ClaimedRelationship.PARENT_CHILD,
        evidence=[_supporting_citation_piece()],
        counter_evidence=[],
        direct_relationship_resolved=True,
        locale="ru",
    )
    assert "родитель и ребёнок" in n
    assert "Композитный confidence" in n


# ---------------------------------------------------------------------------
# Render: HTML
# ---------------------------------------------------------------------------


def test_render_html_supporting_only_skips_counter_table() -> None:
    """Counter-evidence section должна показывать "no contradicting" message."""
    ctx = _ctx(evidence=[_supporting_citation_piece()], counter=[])
    html = render_html(ctx)
    assert "Supporting evidence" in html
    assert "Counter-evidence" in html
    assert "No contradicting evidence found." in html


def test_render_html_includes_counter_table_when_contradicting() -> None:
    ctx = _ctx(
        evidence=[_supporting_citation_piece()],
        counter=[_contradicting_hypothesis_piece()],
    )
    html = render_html(ctx)
    # Counter table present — rule_id-style title
    assert "rule:age_window" in html
    # Counter section heading is highlighted
    assert 'class="negative"' in html


def test_render_html_provenance_block_renders_for_off_catalog() -> None:
    ctx = _ctx(evidence=[_supporting_off_catalog_piece()], counter=[])
    html = render_html(ctx)
    assert "Provenance" in html
    assert "archive_visit" in html
    assert "State Archive of Kyiv Oblast" in html
    assert "$12.50" in html


def test_render_html_extended_claim_shows_caveat_block() -> None:
    ctx = _ctx(claim=ClaimedRelationship.FIRST_COUSIN, direct_resolved=False)
    html = render_html(ctx)
    assert "extended-distance" in html
    assert "first cousins" in html


def test_render_html_unresolved_direct_claim_shows_warning() -> None:
    ctx = _ctx(claim=ClaimedRelationship.PARENT_CHILD, direct_resolved=False)
    html = render_html(ctx)
    assert "NOT found" in html


def test_render_html_footnote_index_dedup() -> None:
    """Один и тот же (source, citation) даёт один footnote-номер, даже если в двух pieces."""
    ref = _citation_ref("Shared Source")
    p1 = EvidencePiece(
        kind="citation",
        severity="supporting",
        title="t1",
        weight=0.5,
        match_certainty=1.0,
        citations=[ref],
    )
    p2 = EvidencePiece(
        kind="citation",
        severity="supporting",
        title="t2",
        weight=0.5,
        match_certainty=1.0,
        citations=[ref],
    )
    ctx = _ctx(evidence=[p1, p2], counter=[])
    html = render_html(ctx)
    # Footnote ordered list имеет ровно один <li>
    assert html.count("<li>") == 1


# ---------------------------------------------------------------------------
# PDF render — best-effort (skip on missing native libs)
# ---------------------------------------------------------------------------


def test_render_pdf_works_or_skips() -> None:
    """PDF byte-length > 5KB если WeasyPrint доступен; иначе skip."""
    ctx = _ctx()
    html = render_html(ctx)
    try:
        pdf = render_pdf(html)
    except PdfRenderError:
        pytest.skip("WeasyPrint native libs unavailable on this host")
    assert isinstance(pdf, bytes)
    assert len(pdf) > 5_000
