"""Интеграционные тесты ``GET /trees/{id}/persons`` и ``GET /persons/{id}``."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


_MINIMAL_GED = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1850
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
0 TRLR
"""


_GED_WITH_PLACE = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 TRLR
"""


_GED_WITH_CITATIONS_AND_MEDIA = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1850
2 SOUR @S1@
3 PAGE p. 42
3 QUAY 3
1 OBJE @M1@
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 @M1@ OBJE
1 FILE photos/john_smith_1850.jpg
1 FORM jpg
1 TITL John Smith portrait, 1850
0 TRLR
"""


# Три поколения: дед/бабка по отцу + дед/бабка по матери, родители, ребёнок.
# Используется в тестах ``GET /persons/{id}/ancestors``.
_GED_3_GENERATIONS = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Alpha /First/
1 SEX M
1 BIRT
2 DATE 1800
1 DEAT
2 DATE 1870
0 @I2@ INDI
1 NAME Bravo /First/
1 SEX F
1 BIRT
2 DATE 1805
0 @I3@ INDI
1 NAME Charlie /Second/
1 SEX M
1 BIRT
2 DATE 1802
0 @I4@ INDI
1 NAME Delta /Second/
1 SEX F
1 BIRT
2 DATE 1808
0 @I5@ INDI
1 NAME Echo /First/
1 SEX M
1 BIRT
2 DATE 1830
0 @I6@ INDI
1 NAME Foxtrot /Second/
1 SEX F
1 BIRT
2 DATE 1832
0 @I7@ INDI
1 NAME Golf /First/
1 SEX M
1 BIRT
2 DATE 1860
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I5@
0 @F2@ FAM
1 HUSB @I3@
1 WIFE @I4@
1 CHIL @I6@
0 @F3@ FAM
1 HUSB @I5@
1 WIFE @I6@
1 CHIL @I7@
0 TRLR
"""


@pytest.mark.asyncio
async def test_list_persons_returns_imported(app_client) -> None:
    """После импорта: ``GET /trees/{id}/persons`` возвращает 2 персон."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    response = await app_client.get(f"/trees/{tree_id}/persons")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert body["tree_id"] == tree_id


@pytest.mark.asyncio
async def test_get_person_detail(app_client) -> None:
    """``GET /persons/{id}`` возвращает имя и хотя бы одно событие BIRT."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    assert items, "no persons returned from list"
    person_id = items[0]["id"]

    response = await app_client.get(f"/persons/{person_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == person_id
    assert len(body["names"]) >= 1


@pytest.mark.asyncio
async def test_get_person_returns_404_for_unknown(app_client) -> None:
    """Несуществующий UUID → 404."""
    response = await app_client.get("/persons/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_person_returns_citations_and_media(app_client) -> None:
    """``GET /persons/{id}`` отдаёт events[].citations и top-level media[].

    После импорта `_GED_WITH_CITATIONS_AND_MEDIA`:
    - BIRT-event у I1 имеет ровно 1 citation с source_title из `sources.title`,
      page_or_section "p. 42", quality 1.0 (QUAY 3 / 3).
    - persona имеет media[] из 1 элемента с title, file_path и format jpg.
    """
    files = {"file": ("test.ged", _GED_WITH_CITATIONS_AND_MEDIA, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    person_id = next(p["id"] for p in listing.json()["items"] if p["gedcom_xref"] == "I1")

    response = await app_client.get(f"/persons/{person_id}")
    assert response.status_code == 200
    body = response.json()

    birt = next(e for e in body["events"] if e["event_type"] == "BIRT")
    assert len(birt["citations"]) == 1
    cit = birt["citations"][0]
    assert cit["source_title"] == "Lubelskie parish records 1838"
    assert cit["page"] == "p. 42"
    assert cit["quality"] == pytest.approx(1.0)

    assert len(body["media"]) == 1
    media = body["media"][0]
    assert media["title"] == "John Smith portrait, 1850"
    assert media["file_path"] == "photos/john_smith_1850.jpg"
    assert media["format"] == "jpg"


@pytest.mark.asyncio
async def test_get_person_returns_event_with_place(app_client) -> None:
    """``GET /persons/{id}`` отдаёт ``events[].place`` с canonical_name.

    Для импорта с PLAC у BIRT-события в ответе должен быть объект place
    с id и name (под алиасом для canonical_name).
    """
    files = {"file": ("test.ged", _GED_WITH_PLACE, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    assert items, "no persons returned"
    person_id = items[0]["id"]

    response = await app_client.get(f"/persons/{person_id}")
    assert response.status_code == 200
    events = response.json()["events"]
    birt = next(e for e in events if e["event_type"] == "BIRT")
    assert birt["place_id"] is not None
    assert birt["place"] is not None
    assert birt["place"]["name"] == "Slonim, Grodno, Russian Empire"
    assert birt["place"]["id"] == birt["place_id"]


@pytest.mark.asyncio
async def test_ancestors_returns_tree_structure(app_client) -> None:
    """``GET /persons/{id}/ancestors`` возвращает рекурсивное дерево.

    На фикстуре `_GED_3_GENERATIONS` для I7 (Golf) ожидаем:
    - root.primary_name == "Golf First"
    - root.father.primary_name == "Echo First", father.father.primary_name == "Alpha First"
    - root.mother.primary_name == "Foxtrot Second", mother.father.primary_name == "Charlie Second"
    - generations_loaded >= 2 (загружены 2 родительских поколения)
    """
    files = {"file": ("test.ged", _GED_3_GENERATIONS, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    person_id = next(p["id"] for p in listing.json()["items"] if p["gedcom_xref"] == "I7")

    response = await app_client.get(f"/persons/{person_id}/ancestors?generations=3")
    assert response.status_code == 200
    body = response.json()

    assert body["person_id"] == person_id
    assert body["generations_requested"] == 3
    assert body["generations_loaded"] >= 2

    root = body["root"]
    assert root["primary_name"] == "Golf First"
    assert root["sex"] == "M"
    assert root["birth_year"] == 1860

    father = root["father"]
    assert father is not None
    assert father["primary_name"] == "Echo First"
    assert father["birth_year"] == 1830
    grandfather = father["father"]
    assert grandfather is not None
    assert grandfather["primary_name"] == "Alpha First"
    assert grandfather["birth_year"] == 1800
    assert grandfather["death_year"] == 1870

    mother = root["mother"]
    assert mother is not None
    assert mother["primary_name"] == "Foxtrot Second"
    maternal_grandfather = mother["father"]
    assert maternal_grandfather is not None
    assert maternal_grandfather["primary_name"] == "Charlie Second"


@pytest.mark.asyncio
async def test_ancestors_returns_404_for_unknown(app_client) -> None:
    """Несуществующий person_id → 404."""
    response = await app_client.get("/persons/00000000-0000-0000-0000-000000000000/ancestors")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ancestors_root_only_when_no_parents(app_client) -> None:
    """Если у персоны нет родителей в дереве: root есть, father/mother == None."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    person_id = items[0]["id"]

    response = await app_client.get(f"/persons/{person_id}/ancestors?generations=5")
    assert response.status_code == 200
    body = response.json()
    assert body["root"]["father"] is None
    assert body["root"]["mother"] is None
    assert body["generations_loaded"] == 0


@pytest.mark.asyncio
async def test_ancestors_rejects_too_many_generations(app_client) -> None:
    """generations > 10 → 422 (FastAPI Query validation)."""
    response = await app_client.get(
        "/persons/00000000-0000-0000-0000-000000000000/ancestors?generations=99"
    )
    assert response.status_code == 422
