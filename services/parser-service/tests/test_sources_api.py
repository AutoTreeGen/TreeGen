"""Phase 3.6 — sources API integration tests.

Маркеры: ``db`` + ``integration`` — testcontainers-postgres.

Покрывают 3 эндпоинта:

* ``GET /trees/{id}/sources`` — пагинированный список Source.
* ``GET /sources/{id}`` — детали + linked entity'ы.
* ``GET /persons/{id}/citations`` — все citations персоны (включая её
  events).
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Минимальный GEDCOM с двумя SOUR + INDI-level и event-level citations.
# Используется во всех трёх тестах ниже.
_GED_WITH_CITATIONS = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 SOUR @S1@
2 PAGE general bio
2 QUAY 1
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
2 SOUR @S2@
3 PAGE p. 42
3 QUAY 3
3 EVEN BIRT
4 ROLE FATH
2 SOUR @S1@
3 PAGE folio 7
3 QUAY 2
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @S1@ SOUR
1 TITL Family bible kept by Anna
1 AUTH Anna Smith
1 ABBR Bible
0 @S2@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
1 PUBL 1898
0 TRLR
"""


@pytest.mark.asyncio
async def test_list_sources_returns_imported_sources(app_client) -> None:
    """``GET /trees/{id}/sources`` возвращает оба SOUR-record после импорта.

    Поля: ``title``, ``abbreviation``, ``author``, ``gedcom_xref`` —
    выставляются согласно SOUR sub-tags из GEDCOM.
    """
    files = {"file": ("test.ged", _GED_WITH_CITATIONS, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    response = await app_client.get(f"/trees/{tree_id}/sources")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["tree_id"] == tree_id
    assert body["total"] == 2
    titles = {s["title"] for s in body["items"]}
    assert titles == {
        "Family bible kept by Anna",
        "Lubelskie parish records 1838",
    }

    # Source S1 имеет ABBR, S2 — нет; gedcom_xref сохранён.
    by_xref = {s["gedcom_xref"]: s for s in body["items"]}
    assert set(by_xref.keys()) == {"S1", "S2"}
    assert by_xref["S1"]["abbreviation"] == "Bible"
    assert by_xref["S1"]["author"] == "Anna Smith"
    assert by_xref["S2"]["publication"] == "1898"
    assert by_xref["S2"]["abbreviation"] is None


@pytest.mark.asyncio
async def test_get_source_returns_linked_entities(app_client) -> None:
    """``GET /sources/{id}`` отдаёт детали + linked entity'и.

    Source @S1@ цитируется дважды: один раз на уровне INDI (page="general bio")
    и один раз на BIRT-событии (page="folio 7"). Source @S2@ цитируется один
    раз на BIRT-событии.
    """
    files = {"file": ("test.ged", _GED_WITH_CITATIONS, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/sources")
    by_xref = {s["gedcom_xref"]: s for s in listing.json()["items"]}
    s1_id = by_xref["S1"]["id"]
    s2_id = by_xref["S2"]["id"]

    # @S1@ — два linked: один person, один event.
    s1_resp = await app_client.get(f"/sources/{s1_id}")
    assert s1_resp.status_code == 200
    s1_body = s1_resp.json()
    assert s1_body["title"] == "Family bible kept by Anna"
    assert s1_body["abbreviation"] == "Bible"
    assert s1_body["gedcom_xref"] == "S1"
    assert len(s1_body["linked"]) == 2
    tables = sorted(item["table"] for item in s1_body["linked"])
    assert tables == ["event", "person"]
    # На person — page="general bio", QUAY=1 → quality≈0.4.
    person_link = next(item for item in s1_body["linked"] if item["table"] == "person")
    assert person_link["page"] == "general bio"
    assert person_link["quay_raw"] == 1
    assert person_link["quality"] == pytest.approx(0.4)

    # @S2@ — один linked event.
    s2_resp = await app_client.get(f"/sources/{s2_id}")
    assert s2_resp.status_code == 200
    s2_body = s2_resp.json()
    assert s2_body["title"] == "Lubelskie parish records 1838"
    assert len(s2_body["linked"]) == 1
    only = s2_body["linked"][0]
    assert only["table"] == "event"
    assert only["page"] == "p. 42"
    assert only["quay_raw"] == 3
    assert only["quality"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_list_person_citations(app_client) -> None:
    """``GET /persons/{id}/citations`` возвращает evidence персоны и её событий.

    У @I1@: 1 INDI-level citation на @S1@ + 2 event-level citations
    на @S2@ и @S1@ (привязаны к BIRT). Итого 3 строки.
    Подтверждаем поля: source_title, page, quay_raw, quality, event_type, role.
    """
    files = {"file": ("test.ged", _GED_WITH_CITATIONS, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    person = next(p for p in listing.json()["items"] if p["gedcom_xref"] == "I1")

    response = await app_client.get(f"/persons/{person['id']}/citations")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["person_id"] == person["id"]
    assert body["total"] == 3

    # Проверим что есть и person-level (source_title=Family bible…), и
    # event-level (на @S2@ — Lubelskie с EVEN/ROLE).
    by_table = {(c["entity_type"], c["page"]): c for c in body["items"]}
    person_cit = by_table[("person", "general bio")]
    assert person_cit["source_title"] == "Family bible kept by Anna"
    assert person_cit["source_abbreviation"] == "Bible"
    assert person_cit["quay_raw"] == 1
    assert person_cit["quality"] == pytest.approx(0.4)

    primary_event_cit = by_table[("event", "p. 42")]
    assert primary_event_cit["source_title"] == "Lubelskie parish records 1838"
    assert primary_event_cit["quay_raw"] == 3
    assert primary_event_cit["quality"] == pytest.approx(0.95)
    assert primary_event_cit["event_type"] == "BIRT"
    assert primary_event_cit["role"] == "FATH"


@pytest.mark.asyncio
async def test_get_source_returns_404_for_unknown(app_client) -> None:
    """Запрос на несуществующий source_id → 404."""
    import uuid

    response = await app_client.get(f"/sources/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_person_citations_returns_404_for_unknown(app_client) -> None:
    """Запрос citations несуществующей персоны → 404."""
    import uuid

    response = await app_client.get(f"/persons/{uuid.uuid4()}/citations")
    assert response.status_code == 404
