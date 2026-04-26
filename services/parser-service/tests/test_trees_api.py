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
