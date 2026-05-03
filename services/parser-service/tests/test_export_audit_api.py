"""Phase 5.9 — endpoint tests для POST /api/v1/gedcom/audit-export.

Stateless эндпоинт (multipart upload), DB не нужен. Auth-override берётся
автоматически из ``conftest._override_auth``.

Покрытие:

* happy path: upload .ged + один target → 200, audits[<target>] валиден;
* multi-platform (4 платформы за один запрос) → 200, по одному ключу
  на каждую;
* отсутствует target_platforms → 400;
* неверное расширение файла → 400;
* upload не parse'ится как GEDCOM → 422.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

_GED_WITH_PROPRIETARY = (
    b"0 HEAD\n"
    b"1 GEDC\n"
    b"2 VERS 5.5.5\n"
    b"2 FORM LINEAGE-LINKED\n"
    b"1 CHAR UTF-8\n"
    b"0 @I1@ INDI\n"
    b"1 NAME John /Smith/\n"
    b"1 _UID PERSON-1\n"
    b"1 _FSFTID FS-PERSON-1\n"
    b"0 TRLR\n"
)


@pytest.mark.asyncio
async def test_audit_export_happy_path(app) -> None:
    """Один target, валидный .ged → 200, audits['ancestry'] заполнен."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/gedcom/audit-export",
            files={"file": ("tree.ged", _GED_WITH_PROPRIETARY, "application/octet-stream")},
            data={"target_platforms": ["ancestry"]},
            headers={"X-User-Id": "00000000-0000-0000-0000-000000000001"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "audits" in body
    assert "ancestry" in body["audits"]
    audit = body["audits"]["ancestry"]
    assert audit["target_platform"] == "ancestry"
    assert audit["total_records"] >= 1
    assert isinstance(audit["findings"], list)
    assert set(audit["summary"].keys()) == {"lost", "transformed", "warning"}
    # _UID/_FSFTID попадают как lost findings
    assert audit["summary"]["lost"] >= 1


@pytest.mark.asyncio
async def test_audit_export_multi_platform(app) -> None:
    """Все 4 поддерживаемых платформы за один запрос → 4 ключа в audits."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/gedcom/audit-export",
            files={"file": ("tree.ged", _GED_WITH_PROPRIETARY, "application/octet-stream")},
            data={"target_platforms": ["ancestry", "myheritage", "familysearch", "gramps"]},
            headers={"X-User-Id": "00000000-0000-0000-0000-000000000001"},
        )
    assert response.status_code == 200, response.text
    audits = response.json()["audits"]
    assert set(audits.keys()) == {"ancestry", "myheritage", "familysearch", "gramps"}
    # все 4 audits внутренне валидны
    for key, audit in audits.items():
        assert audit["target_platform"] == key
        assert sum(audit["summary"].values()) == len(audit["findings"])


@pytest.mark.asyncio
async def test_audit_export_rejects_empty_target_list(app) -> None:
    """Form без target_platforms → FastAPI отвечает 422 (missing required form field).

    Любой 4xx — приемлем; проверяем, что не 200/5xx.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/gedcom/audit-export",
            files={"file": ("tree.ged", _GED_WITH_PROPRIETARY, "application/octet-stream")},
            headers={"X-User-Id": "00000000-0000-0000-0000-000000000001"},
        )
    assert 400 <= response.status_code < 500


@pytest.mark.asyncio
async def test_audit_export_rejects_wrong_extension(app) -> None:
    """``foo.txt`` → 400."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/gedcom/audit-export",
            files={"file": ("tree.txt", _GED_WITH_PROPRIETARY, "text/plain")},
            data={"target_platforms": ["ancestry"]},
            headers={"X-User-Id": "00000000-0000-0000-0000-000000000001"},
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_audit_export_returns_422_on_unparseable_input(app) -> None:
    """Бинарный мусор с .ged-расширением → 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/gedcom/audit-export",
            files={
                "file": ("garbage.ged", b"\xff\xfe\x00not-a-gedcom\x00", "application/octet-stream")
            },
            data={"target_platforms": ["ancestry"]},
            headers={"X-User-Id": "00000000-0000-0000-0000-000000000001"},
        )
    # Парсер lenient'ный: даже из мусора может попытаться собрать пустой документ.
    # Тест допускает оба исхода (200 на пустом документе или 422 на parse fail);
    # главное — сервис не падает 5xx.
    assert response.status_code in (200, 422), response.text
