"""Интеграционные тесты ``POST /imports`` + ``GET /imports/{id}``.

Маркеры: ``db`` + ``integration`` — пропускаются если testcontainers нет.
"""

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
0 @I2@ INDI
1 NAME Mary /Smith/
1 SEX F
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
0 TRLR
"""


@pytest.mark.asyncio
async def test_post_import_creates_job_and_persons(app_client) -> None:
    """Загрузка минимального .ged → 201 + status=succeeded + 2 persons."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["stats"]["persons"] == 2
    assert body["stats"]["families"] == 1
    assert body["source_filename"] == "test.ged"
    assert body["tree_id"] is not None
    assert body["id"] is not None


@pytest.mark.asyncio
async def test_post_import_rejects_non_gedcom_file(app_client) -> None:
    """Файл с расширением не .ged/.gedcom → 400."""
    files = {"file": ("test.txt", b"not a gedcom", "text/plain")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_import_returns_existing_job(app_client) -> None:
    """Создаём job, потом достаём его по id."""
    files = {"file": ("test.ged", _MINIMAL_GED, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    job_id = created.json()["id"]

    response = await app_client.get(f"/imports/{job_id}")
    assert response.status_code == 200
    assert response.json()["id"] == job_id


@pytest.mark.asyncio
async def test_get_import_returns_404_for_unknown_job(app_client) -> None:
    """Несуществующий UUID → 404."""
    response = await app_client.get("/imports/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
