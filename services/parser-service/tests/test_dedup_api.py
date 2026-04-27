"""HTTP тесты ``GET /trees/{id}/duplicate-suggestions`` (Phase 3.4 Task 5)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


_GED_DEDUP = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim, Grodno, Russian Empire
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 @S1@ SOUR
1 TITL Lubelskie parish records 1838
1 AUTH Lubelskie Archive
0 TRLR
"""


async def _import_and_get_tree_id(app_client) -> str:
    files = {"file": ("test.ged", _GED_DEDUP, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 201, response.text
    return response.json()["tree_id"]


@pytest.mark.asyncio
async def test_get_dedup_suggestions_returns_pairs(app_client) -> None:
    """Базовый endpoint без фильтра возвращает пары и метаданные."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(f"/trees/{tree_id}/duplicate-suggestions")
    assert response.status_code == 200
    body = response.json()
    assert body["tree_id"] == tree_id
    assert body["min_confidence"] == 0.80
    assert body["limit"] == 100
    assert body["offset"] == 0
    assert isinstance(body["items"], list)
    # Должна быть как минимум одна пара (Zhitnitzky / Zhytnicki).
    assert body["total"] >= 1
    assert body["items"], "expected at least one suggestion"
    # Caждая пара содержит required поля.
    for item in body["items"]:
        assert "entity_type" in item
        assert item["entity_type"] in ("source", "place", "person")
        assert "entity_a_id" in item
        assert "entity_b_id" in item
        assert 0.0 <= item["confidence"] <= 1.0


@pytest.mark.asyncio
async def test_filter_by_entity_type_person(app_client) -> None:
    """``?entity_type=person`` отдаёт только person-пары."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"entity_type": "person"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entity_type"] == "person"
    for item in body["items"]:
        assert item["entity_type"] == "person"


@pytest.mark.asyncio
async def test_filter_by_entity_type_place(app_client) -> None:
    """``?entity_type=place`` отдаёт только place-пары."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"entity_type": "place"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["entity_type"] == "place"
    for item in body["items"]:
        assert item["entity_type"] == "place"


@pytest.mark.asyncio
async def test_min_confidence_threshold(app_client) -> None:
    """Высокий threshold отсекает слабые пары."""
    tree_id = await _import_and_get_tree_id(app_client)
    high = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"min_confidence": 0.99},
    )
    low = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"min_confidence": 0.50},
    )
    assert high.status_code == 200
    assert low.status_code == 200
    assert high.json()["total"] <= low.json()["total"]
    for item in high.json()["items"]:
        assert item["confidence"] >= 0.99


@pytest.mark.asyncio
async def test_pagination(app_client) -> None:
    """``limit`` / ``offset`` корректно режут выдачу."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"min_confidence": 0.50, "limit": 1, "offset": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 1
    assert len(body["items"]) <= 1


@pytest.mark.asyncio
async def test_invalid_entity_type_returns_422(app_client) -> None:
    """Неизвестный entity_type → 422 (FastAPI Literal валидирует)."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"entity_type": "garbage"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_invalid_confidence_returns_422(app_client) -> None:
    """``min_confidence`` за границами [0, 1] → 422."""
    tree_id = await _import_and_get_tree_id(app_client)
    response = await app_client.get(
        f"/trees/{tree_id}/duplicate-suggestions",
        params={"min_confidence": 1.5},
    )
    assert response.status_code == 422
