"""End-to-end FastAPI тесты archive registry endpoints (Phase 22.1).

Repo замокан in-memory (см. ``conftest.patch_repo``); тесты гонят на
HTTP-уровне через httpx ASGI client. Покрывают:

* GET /archives/registry — фильтр + ranking + privacy_blocked
* GET /archives/registry/{id} — single fetch + 404
* POST/PATCH/DELETE — admin-guard + happy-path
* Naum Katz сценарий — anti-regression на origin owner case
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

# ── GET /archives/registry — search ───────────────────────────────────────


@pytest.mark.asyncio
async def test_search_unfiltered_returns_all(registry_client: AsyncClient) -> None:
    """Без фильтров — все 5 fixture listing'ов в ответе."""
    resp = await registry_client.get("/archives/registry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5


@pytest.mark.asyncio
async def test_search_country_filter(registry_client: AsyncClient) -> None:
    """country=UA → 2 listing'а (SBU Lviv + DAZHO)."""
    resp = await registry_client.get("/archives/registry", params={"country": "UA"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    countries = {it["country"] for it in body["items"]}
    assert countries == {"UA"}


@pytest.mark.asyncio
async def test_search_record_type_filter(registry_client: AsyncClient) -> None:
    """record_type=civil_birth → DAZHO (UA) + Standesamt (DE) = 2."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"record_type": "civil_birth"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = {it["name"] for it in body["items"]}
    assert "DAZHO — Zhytomyr oblast archive" in names
    assert "Standesamt Berlin Mitte" in names


@pytest.mark.asyncio
async def test_search_country_and_record_type_combined(registry_client: AsyncClient) -> None:
    """country=UA + record_type=metric_book → DAZHO."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"country": "UA", "record_type": "metric_book"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "DAZHO — Zhytomyr oblast archive"


@pytest.mark.asyncio
async def test_search_invalid_year_range_returns_422(registry_client: AsyncClient) -> None:
    """year_to < year_from → 422."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"year_from": 1950, "year_to": 1900},
    )
    assert resp.status_code == 422


# ── ranking ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ranking_exact_record_type_match_first(registry_client: AsyncClient) -> None:
    """SBU Lviv (passport_internal exact) ранжируется выше DAZHO."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"country": "UA", "record_type": "passport_internal"},
    )
    body = resp.json()
    assert body["items"][0]["name"] == "SBU oblast archive Lviv"


@pytest.mark.asyncio
async def test_ranking_year_overlap_breaks_tie(registry_client: AsyncClient) -> None:
    """С узким year window — DAZHO ниже потому что не покрывает 1980."""
    # Запрос country=UA + year [1970,1990] — SBU Lviv overlap'ы есть, DAZHO нет.
    resp = await registry_client.get(
        "/archives/registry",
        params={"country": "UA", "year_from": 1970, "year_to": 1990},
    )
    body = resp.json()
    sbu = next(it for it in body["items"] if it["name"].startswith("SBU"))
    dazho = next(it for it in body["items"] if it["name"].startswith("DAZHO"))
    assert sbu["rank_score"] >= dazho["rank_score"]


# ── privacy_blocked flag ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_privacy_blocked_when_query_in_window(registry_client: AsyncClient) -> None:
    """Standesamt privacy_window=110, query year_to=2000 → blocked."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"country": "DE", "year_from": 1990, "year_to": 2000},
    )
    body = resp.json()
    standesamt = next(it for it in body["items"] if it["name"].startswith("Standesamt"))
    assert standesamt["privacy_blocked"] is True


@pytest.mark.asyncio
async def test_privacy_not_blocked_outside_window(registry_client: AsyncClient) -> None:
    """SBU privacy_window=75, query [1900,1930] → not blocked (cutoff 1951)."""
    resp = await registry_client.get(
        "/archives/registry",
        params={"country": "UA", "year_from": 1900, "year_to": 1930},
    )
    body = resp.json()
    sbu = next(it for it in body["items"] if it["name"].startswith("SBU"))
    assert sbu["privacy_blocked"] is False


# ── single GET ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_listing_by_id(registry_client: AsyncClient, patch_repo: dict[str, Any]) -> None:
    """GET /{id} возвращает один listing."""
    listing_id = next(iter(patch_repo["state"]))
    resp = await registry_client.get(f"/archives/registry/{listing_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == listing_id


@pytest.mark.asyncio
async def test_get_listing_404(registry_client: AsyncClient) -> None:
    """Unknown UUID → 404."""
    resp = await registry_client.get(
        "/archives/registry/00000000-0000-4000-8000-000000000000",
    )
    assert resp.status_code == 404


# ── admin guard ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_forbidden_for_non_admin(
    registry_client: AsyncClient,
    make_listing_payload,
) -> None:
    """POST с anonymous claims (email=None) → 403."""
    resp = await registry_client.post(
        "/archives/registry",
        json=make_listing_payload(),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_patch_forbidden_for_non_admin(
    registry_client: AsyncClient,
    patch_repo: dict[str, Any],
) -> None:
    """PATCH с anonymous claims → 403."""
    listing_id = next(iter(patch_repo["state"]))
    resp = await registry_client.patch(
        f"/archives/registry/{listing_id}",
        json={"notes": "updated by hacker"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_forbidden_for_non_admin(
    registry_client: AsyncClient,
    patch_repo: dict[str, Any],
) -> None:
    """DELETE с anonymous claims → 403."""
    listing_id = next(iter(patch_repo["state"]))
    resp = await registry_client.delete(f"/archives/registry/{listing_id}")
    assert resp.status_code == 403


# ── admin CRUD happy-path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_create_listing_admin(
    registry_client_admin: AsyncClient,
    make_listing_payload,
) -> None:
    """POST с admin email → 201 + listing в state."""
    resp = await registry_client_admin.post(
        "/archives/registry",
        json=make_listing_payload(name="New SBU oblast archive Kharkiv"),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "New SBU oblast archive Kharkiv"
    assert body["country"] == "UA"


@pytest.mark.asyncio
async def test_patch_update_listing_admin(
    registry_client_admin: AsyncClient,
    patch_repo: dict[str, Any],
) -> None:
    """PATCH /{id} с admin email → 200 + поле обновлено."""
    listing_id = next(iter(patch_repo["state"]))
    resp = await registry_client_admin.patch(
        f"/archives/registry/{listing_id}",
        json={"notes": "verified via SBU response 2026-05-03"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["notes"] == "verified via SBU response 2026-05-03"


@pytest.mark.asyncio
async def test_delete_listing_admin(
    registry_client_admin: AsyncClient,
    patch_repo: dict[str, Any],
) -> None:
    """DELETE с admin email → 204 + listing удалён."""
    listing_id = next(iter(patch_repo["state"]))
    resp = await registry_client_admin.delete(f"/archives/registry/{listing_id}")
    assert resp.status_code == 204
    assert listing_id not in patch_repo["state"]


# ── Naum Katz anti-regression ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_naum_katz_query_returns_sbu_lviv(registry_client: AsyncClient) -> None:
    """Origin scenario: query (UA, passport_internal, 1900-1950) →
    SBU oblast archive Lviv должен быть top-1 результат.

    Это origin для всей Phase 22.x — owner заплатил $100 SBU за паспортный
    запрос Naum Katz (Konyukhi/Hrubieszów). Query, который должен был
    привести к этой же подсказке через registry.
    """
    resp = await registry_client.get(
        "/archives/registry",
        params={
            "country": "UA",
            "record_type": "passport_internal",
            "year_from": 1900,
            "year_to": 1950,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    top = body["items"][0]
    assert top["name"] == "SBU oblast archive Lviv"
    # Privacy window 75 + today=2026 → cutoff 1951; query [1900,1950] под cutoff.
    assert top["privacy_blocked"] is False
    # Fee range экспозируется UI'ю: $50-150.
    assert top["fee_min_usd"] == 50
    assert top["fee_max_usd"] == 150
