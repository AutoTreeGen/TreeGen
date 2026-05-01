"""Unit-тесты Court-Ready Report (без БД, без docker).

Тестируют чистые функции: Chicago citation formatter, locale lookup,
HTML render через synthetic ``ReportContext``, footnote-индексирование,
negative findings derivation. Покрывают core логику без integration overhead.

Integration tests с docker + testcontainers — в ``test_render_basic.py``,
выполняются на CI ubuntu-latest.
"""

from __future__ import annotations

import datetime as dt
import uuid

from parser_service.court_ready.citations import format_chicago
from parser_service.court_ready.data import derive_negative_findings
from parser_service.court_ready.locale import (
    confidence_method_label,
    event_label,
    relation_label,
    scope_label,
    sex_label,
    t,
)
from parser_service.court_ready.models import (
    CitationRef,
    EventClaim,
    NegativeFinding,
    RelationshipClaim,
    ReportContext,
    SubjectSummary,
)
from parser_service.court_ready.render import render_html


def _make_citation(
    *,
    title: str = "Test source",
    author: str | None = None,
    page: str | None = None,
    quoted: str | None = None,
    quality: float = 0.7,
    quay_raw: int | None = None,
    repository: str | None = None,
) -> CitationRef:
    return CitationRef(
        source_id=uuid.uuid4(),
        citation_id=uuid.uuid4(),
        source_title=title,
        author=author,
        repository=repository,
        page_or_section=page,
        quoted_text=quoted,
        quality=quality,
        quay_raw=quay_raw,
    )


def _make_context(**overrides) -> ReportContext:
    """Фабрика minimal-контекста."""
    base = {
        "report_id": uuid.uuid4(),
        "generated_at": dt.datetime(2026, 5, 1, 12, 0, tzinfo=dt.UTC),
        "locale": "en",
        "scope": "person",
        "tree_id": uuid.uuid4(),
        "tree_name": "Test Tree",
        "subject": SubjectSummary(
            person_id=uuid.uuid4(),
            primary_name="Test Subject",
            sex="M",
        ),
        "methodology_statement": "Test methodology.",
    }
    base.update(overrides)
    return ReportContext(**base)


# ---------------------------------------------------------------------------
# Chicago citation formatter
# ---------------------------------------------------------------------------


def test_chicago_minimal_title_only() -> None:
    cit = _make_citation(title="Pinkas Grodno")
    out = format_chicago(cit)
    assert "Pinkas Grodno" in out
    assert out.endswith(".")


def test_chicago_with_author_and_page() -> None:
    cit = _make_citation(
        title="Birth Register 1850",
        author="State Archive of Grodno",
        page="folio 42",
        repository="Grodno Eparchial Archive",
    )
    out = format_chicago(cit)
    assert "State Archive of Grodno" in out
    assert "Birth Register 1850" in out
    assert "folio 42" in out
    assert "Grodno Eparchial Archive" in out


def test_chicago_quay_3_appends_primary_marker() -> None:
    cit = _make_citation(title="Birth record", quay_raw=3)
    out = format_chicago(cit)
    assert "primary source" in out


def test_chicago_quoted_excerpt_truncated() -> None:
    cit = _make_citation(title="Long source", quoted="x" * 500)
    out = format_chicago(cit)
    # 200-char limit → 197 + "..."
    assert "..." in out
    assert out.count("x") <= 200


def test_chicago_no_quay_no_marker() -> None:
    cit = _make_citation(title="Only title", quay_raw=None)
    out = format_chicago(cit)
    assert "primary source" not in out
    assert "secondary source" not in out


# ---------------------------------------------------------------------------
# Locale lookups
# ---------------------------------------------------------------------------


def test_locale_event_label_birt_en_ru() -> None:
    assert event_label("BIRT", "en") == "Birth"
    assert event_label("BIRT", "ru") == "Рождение"


def test_locale_custom_event_uses_custom_type() -> None:
    assert event_label("CUSTOM", "en", custom_type="Bar Mitzvah") == "Bar Mitzvah"


def test_locale_unknown_event_returns_raw() -> None:
    assert event_label("XYZWQ", "en") == "XYZWQ"


def test_sex_label_known_and_unknown() -> None:
    assert sex_label("M", "en") == "Male"
    assert sex_label("F", "ru") == "Женский"
    assert sex_label("?", "en") == "Unknown"


def test_relation_label_all_kinds() -> None:
    for kind in ("parent", "child", "spouse", "sibling"):
        assert relation_label(kind, "en")
        assert relation_label(kind, "ru")


def test_confidence_method_label_all() -> None:
    for method in ("bayesian_fusion_v2", "naive_count", "asserted_only"):
        assert confidence_method_label(method, "en")
        assert confidence_method_label(method, "ru")


def test_scope_label_ancestry_substitutes_n() -> None:
    out = scope_label("ancestry_to_gen", target_gen=3, locale="en")
    assert "3" in out


def test_t_returns_translated_strings() -> None:
    assert t("subject", "en") == "Subject"
    assert t("subject", "ru") == "Субъект"


# ---------------------------------------------------------------------------
# Negative findings derivation
# ---------------------------------------------------------------------------


def test_negative_findings_event_without_source() -> None:
    subject = SubjectSummary(person_id=uuid.uuid4(), primary_name="X", sex="M")
    event = EventClaim(event_id=uuid.uuid4(), event_type="OCCU", citations=[])
    findings = derive_negative_findings(subject=subject, events=[event], relationships=[])
    kinds = {f.kind for f in findings}
    assert "event_without_source" in kinds


def test_negative_findings_relationship_asserted_only() -> None:
    subject = SubjectSummary(person_id=uuid.uuid4(), primary_name="X", sex="M")
    rel = RelationshipClaim(
        relation_kind="parent",
        other_person_id=uuid.uuid4(),
        other_person_name="Other",
        evidence_type="asserted_only",
        confidence_score=0.0,
        confidence_method="asserted_only",
        citations=[],
    )
    findings = derive_negative_findings(subject=subject, events=[], relationships=[rel])
    kinds = {f.kind for f in findings}
    assert "relationship_without_evidence" in kinds


def test_negative_findings_missing_vital_when_birth_and_death_absent() -> None:
    subject = SubjectSummary(person_id=uuid.uuid4(), primary_name="X", sex="M")
    findings = derive_negative_findings(subject=subject, events=[], relationships=[])
    descriptions = [f.description for f in findings if f.kind == "missing_vital"]
    assert any("BIRT" in d for d in descriptions)
    assert any("DEAT" in d for d in descriptions)


def test_negative_findings_empty_when_everything_sourced() -> None:
    subject = SubjectSummary(
        person_id=uuid.uuid4(),
        primary_name="X",
        sex="M",
        birth=EventClaim(
            event_id=uuid.uuid4(),
            event_type="BIRT",
            citations=[_make_citation()],
        ),
        death=EventClaim(
            event_id=uuid.uuid4(),
            event_type="DEAT",
            citations=[_make_citation()],
        ),
    )
    rel = RelationshipClaim(
        relation_kind="parent",
        other_person_id=uuid.uuid4(),
        other_person_name="Parent",
        evidence_type="citation",
        confidence_score=0.9,
        confidence_method="bayesian_fusion_v2",
        citations=[_make_citation()],
    )
    findings = derive_negative_findings(
        subject=subject,
        events=[subject.birth, subject.death],  # type: ignore[list-item]
        relationships=[rel],
    )
    assert findings == []


# ---------------------------------------------------------------------------
# HTML render
# ---------------------------------------------------------------------------


def test_render_html_minimal_context() -> None:
    ctx = _make_context()
    html = render_html(ctx)
    assert "<!doctype html>" in html.lower()
    assert "Test Subject" in html
    assert "Court-Ready Genealogical Report" in html
    assert "Test Tree" in html


def test_render_html_includes_footnotes_when_citations_present() -> None:
    cit = _make_citation(title="Source A", page="p. 1")
    birth = EventClaim(
        event_id=uuid.uuid4(),
        event_type="BIRT",
        date_raw="ABT 1850",
        citations=[cit],
    )
    subject = SubjectSummary(
        person_id=uuid.uuid4(),
        primary_name="With Citation",
        sex="M",
        birth=birth,
    )
    ctx = _make_context(subject=subject)
    html = render_html(ctx)
    assert "Source A" in html
    assert "<sup>1</sup>" in html
    assert "Footnotes" in html


def test_render_html_locale_ru_translates_headers() -> None:
    ctx = _make_context(locale="ru")
    html = render_html(ctx)
    assert "Сводка по субъекту" in html
    assert "Доказательная цепочка" in html
    assert 'lang="ru"' in html


def test_render_html_family_template_renders() -> None:
    ctx = _make_context(scope="family")
    html = render_html(ctx)
    assert "Family unit" in html or "Court-Ready" in html


def test_render_html_ancestry_template_renders() -> None:
    ancestor = SubjectSummary(
        person_id=uuid.uuid4(),
        primary_name="Grandfather X",
        sex="M",
    )
    ctx = _make_context(scope="ancestry_to_gen", target_gen=2, ancestry=[ancestor])
    html = render_html(ctx)
    assert "Grandfather X" in html
    assert "Ancestry" in html


def test_render_html_negative_findings_section_visible() -> None:
    nf = NegativeFinding(kind="missing_vital", description="BIRT (no event recorded)")
    ctx = _make_context(negative_findings=[nf])
    html = render_html(ctx)
    assert "Missing vital record" in html
    assert "BIRT" in html


def test_render_html_no_relationships_shows_empty_state() -> None:
    ctx = _make_context()
    html = render_html(ctx)
    assert "No relationships recorded." in html


def test_render_html_signature_block_includes_researcher() -> None:
    ctx = _make_context(researcher_name="Vald the Researcher")
    html = render_html(ctx)
    assert "Vald the Researcher" in html
    assert "Methodology" in html


# ---------------------------------------------------------------------------
# Footnote indexing
# ---------------------------------------------------------------------------


def test_footnote_indexing_dedupe_same_citation() -> None:
    """Один и тот же (source, citation) встретившийся в двух разных claim'ах
    получает один footnote-номер."""
    shared_cit = _make_citation(title="Shared")
    birth = EventClaim(
        event_id=uuid.uuid4(),
        event_type="BIRT",
        citations=[shared_cit],
    )
    death = EventClaim(
        event_id=uuid.uuid4(),
        event_type="DEAT",
        citations=[shared_cit],
    )
    subject = SubjectSummary(
        person_id=uuid.uuid4(),
        primary_name="Dup Test",
        sex="M",
        birth=birth,
        death=death,
    )
    ctx = _make_context(subject=subject)
    html = render_html(ctx)
    # Both events refer to <sup>1</sup>; <sup>2</sup> should not appear.
    assert "<sup>1</sup>" in html
    assert "<sup>2</sup>" not in html
