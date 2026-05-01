"""Phase 15.6 — рендер Court-Ready Report (HTML + PDF).

Покрытие:

* Базовый рендер: person с 3 events, 5 citations, 2 relationships.
* Negative findings: person без citations.
* HTML snapshot: структурные инварианты (без жёстко-зашитого golden).
* Endpoint POST /api/v1/reports/court-ready возвращает 200 + valid PDF.
* Family scope: рендер /family.html без падения.
* Ancestry scope: рендер /ancestry.html с 2 поколениями.

PDF-тесты используют ``pytest.skip`` если WeasyPrint native libs
недоступны (Windows-dev без GTK runtime).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from parser_service.court_ready.data import build_report_context
from parser_service.court_ready.render import (
    PdfRenderError,
    render_html,
    render_pdf,
)
from shared_models.orm import EventParticipant
from sqlalchemy import update

from .conftest import (
    add_child,
    hdr,
    make_event,
    make_family,
    make_person,
    make_place,
    make_source_and_citation,
    make_tree,
    make_user,
)

pytestmark = [pytest.mark.db, pytest.mark.integration]


def _pdf_supported() -> bool:
    """Quick probe: попробовать рендер пустой страницы. False — нет native libs."""
    try:
        render_pdf("<!doctype html><html><body><p>x</p></body></html>")
    except PdfRenderError:
        return False
    return True


# ---------------------------------------------------------------------------
# Базовый рендер
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_basic_person_3_events_5_citations_2_relationships(
    session_factory: Any,
) -> None:
    """3 events + 5 citations + 2 relationships → HTML рендер не падает,
    PDF (если supported) > 5 KB."""
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree, given="Sigmund", surname="Levitin")
    parent = await make_person(session_factory, tree=tree, given="Yakov", surname="Levitin")
    spouse = await make_person(session_factory, tree=tree, given="Anna", surname="Goldman", sex="F")

    place = await make_place(session_factory, tree=tree, name="Grodno, Russian Empire")
    birth = await make_event(
        session_factory,
        tree=tree,
        person=person,
        event_type="BIRT",
        date_start=dt.date(1850, 5, 12),
        date_raw="ABT 1850",
        place=place,
    )
    death = await make_event(
        session_factory,
        tree=tree,
        person=person,
        event_type="DEAT",
        date_start=dt.date(1920, 3, 1),
        place=place,
    )
    occu = await make_event(
        session_factory,
        tree=tree,
        person=person,
        event_type="OCCU",
        description="Watchmaker",
    )

    # 5 citations: 2 on birth, 1 on death, 1 on occu, 1 on parent-family.
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="event",
        entity_id=birth.id,
        title="Grodno birth register 1850",
        repository="State Archive of Grodno",
        page="folio 42",
        snippet="Sigmund b. of Yakov",
        quality=0.95,
        quay_raw=3,
    )
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="event",
        entity_id=birth.id,
        title="Family bible (Levitin)",
        author="Levitin family",
        page="leaf 2",
        quality=0.5,
    )
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="event",
        entity_id=death.id,
        title="Death record 1920",
        repository="State Archive of Grodno",
        quality=0.7,
    )
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="event",
        entity_id=occu.id,
        title="Census 1897",
        quality=0.4,
    )

    # parent_child relationship
    parent_family = await make_family(session_factory, tree=tree, husband=parent)
    await add_child(session_factory, family=parent_family, child=person)
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="family",
        entity_id=parent_family.id,
        title="Parish register Grodno",
        repository="Grodno Eparchial Archive",
        quality=0.85,
    )

    # spouse relationship (no citation — naive_count + asserted)
    await make_family(session_factory, tree=tree, husband=person, wife=spouse)

    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=person.id,
            scope="person",
            target_gen=None,
            locale="en",
        )

    # Subject + events sanity
    assert ctx.subject.primary_name == "Sigmund Levitin"
    assert ctx.subject.birth is not None
    assert ctx.subject.death is not None
    assert len(ctx.subject.birth.citations) == 2
    assert len(ctx.subject.death.citations) == 1

    # Relationships: parent (with citation) + child (none, the spouse) + spouse asserted-only.
    rel_kinds = {r.relation_kind for r in ctx.relationships}
    assert "parent" in rel_kinds
    assert "spouse" in rel_kinds

    html = render_html(ctx)
    assert "Sigmund Levitin" in html
    assert "Grodno birth register 1850" in html
    assert "Yakov Levitin" in html
    assert "Anna Goldman" in html
    # Footnote indices are sequential; at least 5 unique footnotes expected.
    assert html.count("<sup>") >= 5

    if not _pdf_supported():
        pytest.skip("WeasyPrint native libs unavailable on this platform")
    pdf = render_pdf(html)
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5_000, f"PDF too small ({len(pdf)} bytes)"


# ---------------------------------------------------------------------------
# Negative findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_person_with_no_sources_produces_negative_findings(
    session_factory: Any,
) -> None:
    """Person с 1 event но без citations → negative_findings список заполнен."""
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree)
    await make_event(
        session_factory,
        tree=tree,
        person=person,
        event_type="BIRT",
        date_start=dt.date(1900, 1, 1),
    )
    # Нет DEAT, нет родителей, нет супруга — должны попасть три типа findings:
    # 1× event_without_source (BIRT), 1× missing_vital (DEAT).
    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=person.id,
            scope="person",
            target_gen=None,
            locale="en",
        )

    kinds = {nf.kind for nf in ctx.negative_findings}
    assert "event_without_source" in kinds
    assert "missing_vital" in kinds

    html = render_html(ctx)
    assert "Negative findings" in html
    assert "Event without source" in html
    assert "Missing vital record" in html


# ---------------------------------------------------------------------------
# HTML snapshot — структурные инварианты (без жёсткого golden, чтобы рендер
# можно было итеративно улучшать без перезаписи fixture'а на каждый PR).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_html_snapshot_invariants(session_factory: Any) -> None:
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner, name="Snapshot Tree")
    person = await make_person(session_factory, tree=tree, given="Snapshot", surname="Subject")
    birth = await make_event(
        session_factory,
        tree=tree,
        person=person,
        event_type="BIRT",
        date_raw="ABT 1850",
    )
    await make_source_and_citation(
        session_factory,
        tree=tree,
        entity_type="event",
        entity_id=birth.id,
        title="Pinkas Grodno",
        author="Kahal Grodno",
        page="p. 17",
        quality=0.9,
        quay_raw=3,
    )

    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=person.id,
            scope="person",
            target_gen=None,
            locale="en",
            researcher_name="Vald the Researcher",
        )

    html = render_html(ctx)

    # Structural invariants — must remain stable as report evolves.
    assert "<!doctype html>" in html.lower()
    assert "<html" in html.lower()
    assert "Court-Ready Genealogical Report" in html
    assert "Subject summary" in html
    assert "Family relationships" in html
    assert "Evidence trail" in html
    assert "Negative findings" in html
    assert "Footnotes" in html
    assert "Signature & methodology" in html
    # Methodology + researcher block
    assert "Vald the Researcher" in html
    # Citation in footnotes
    assert "Pinkas Grodno" in html
    # Locale rendered as html lang
    assert 'lang="en"' in html
    # Report ID rendered
    assert str(ctx.report_id) in html


@pytest.mark.asyncio
async def test_html_snapshot_locale_ru(session_factory: Any) -> None:
    """ru locale переводит section-headers и labels."""
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree, given="Сигизмунд", surname="Левитин")

    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=person.id,
            scope="person",
            target_gen=None,
            locale="ru",
        )
    html = render_html(ctx)
    assert "Сводка по субъекту" in html
    assert "Доказательная цепочка" in html
    assert "Отрицательные результаты" in html
    assert 'lang="ru"' in html


# ---------------------------------------------------------------------------
# Family scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_family_scope_renders(session_factory: Any) -> None:
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    husband = await make_person(session_factory, tree=tree, given="Yakov", surname="L")
    wife = await make_person(session_factory, tree=tree, given="Sara", surname="L", sex="F")
    child = await make_person(session_factory, tree=tree, given="Sigmund", surname="L")
    family = await make_family(session_factory, tree=tree, husband=husband, wife=wife)
    await add_child(session_factory, family=family, child=child)

    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=husband.id,
            scope="family",
            target_gen=None,
            locale="en",
        )

    html = render_html(ctx)
    # family.html шаблон рендерит дополнительный block с Spouse / Children sub-headers
    assert "Sara L" in html
    assert "Sigmund L" in html


# ---------------------------------------------------------------------------
# Ancestry scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ancestry_scope_two_generations(session_factory: Any) -> None:
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    me = await make_person(session_factory, tree=tree, given="Me", surname="X")
    dad = await make_person(session_factory, tree=tree, given="Dad", surname="X")
    grandpa = await make_person(session_factory, tree=tree, given="Grandpa", surname="X")
    f1 = await make_family(session_factory, tree=tree, husband=dad)
    await add_child(session_factory, family=f1, child=me)
    f2 = await make_family(session_factory, tree=tree, husband=grandpa)
    await add_child(session_factory, family=f2, child=dad)

    async with session_factory() as session:
        ctx = await build_report_context(
            session,
            person_id=me.id,
            scope="ancestry_to_gen",
            target_gen=2,
            locale="en",
        )

    assert {a.primary_name for a in ctx.ancestry} == {"Dad X", "Grandpa X"}

    html = render_html(ctx)
    assert "Ancestry" in html
    assert "Dad X" in html
    assert "Grandpa X" in html


# ---------------------------------------------------------------------------
# Endpoint integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_returns_200_and_pdf_url(app, app_client, session_factory: Any) -> None:
    """POST /api/v1/reports/court-ready → 200 + report_id + pdf_url + expires_at."""
    if not _pdf_supported():
        pytest.skip("WeasyPrint native libs unavailable on this platform")

    # Override storage to InMemoryStorage so test does not touch real S3/GCS.
    from parser_service.court_ready.api import get_report_storage
    from shared_models.storage import InMemoryStorage

    storage = InMemoryStorage()
    app.dependency_overrides[get_report_storage] = lambda: storage
    try:
        owner = await make_user(session_factory)
        tree = await make_tree(session_factory, owner=owner)
        person = await make_person(session_factory, tree=tree, given="Endpoint", surname="Subject")
        await make_event(
            session_factory,
            tree=tree,
            person=person,
            event_type="BIRT",
            date_start=dt.date(1900, 6, 1),
        )

        resp = await app_client.post(
            "/api/v1/reports/court-ready",
            json={
                "person_id": str(person.id),
                "scope": "person",
                "locale": "en",
            },
            headers=hdr(owner),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "report_id" in body
        assert body["pdf_url"].startswith("memory://")
        assert "expires_at" in body
        # Verify PDF was actually written.
        report_id = uuid.UUID(body["report_id"])
        key = f"court-ready-reports/{person.id}/{report_id}.pdf"
        pdf_bytes = await storage.get(key)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 5_000
    finally:
        app.dependency_overrides.pop(get_report_storage, None)


@pytest.mark.asyncio
async def test_endpoint_returns_404_for_unknown_person(app_client) -> None:
    resp = await app_client.post(
        "/api/v1/reports/court-ready",
        json={"person_id": str(uuid.uuid4()), "scope": "person", "locale": "en"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_endpoint_returns_403_for_non_member(app_client, session_factory: Any) -> None:
    owner = await make_user(session_factory)
    intruder = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree)

    resp = await app_client.post(
        "/api/v1/reports/court-ready",
        json={"person_id": str(person.id), "scope": "person", "locale": "en"},
        headers=hdr(intruder),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_endpoint_400_when_ancestry_missing_target_gen(
    app_client, session_factory: Any
) -> None:
    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree)

    resp = await app_client.post(
        "/api/v1/reports/court-ready",
        json={"person_id": str(person.id), "scope": "ancestry_to_gen", "locale": "en"},
        headers=hdr(owner),
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Edge: soft-deleted person → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_deleted_person_404(app_client, session_factory: Any) -> None:
    from shared_models.orm import Person

    owner = await make_user(session_factory)
    tree = await make_tree(session_factory, owner=owner)
    person = await make_person(session_factory, tree=tree)
    async with session_factory() as session:
        await session.execute(
            update(Person).where(Person.id == person.id).values(deleted_at=dt.datetime.now(dt.UTC))
        )
        await session.commit()

    resp = await app_client.post(
        "/api/v1/reports/court-ready",
        json={"person_id": str(person.id), "scope": "person", "locale": "en"},
        headers=hdr(owner),
    )
    assert resp.status_code == 404
    # EventParticipant import не используется напрямую — подавляет линтер unused.
    _ = EventParticipant
