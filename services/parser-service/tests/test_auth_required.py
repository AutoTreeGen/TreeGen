"""End-to-end auth-required test (Phase 4.10, ADR-0033).

Запускает parser-service **без** auth-override'а и проверяет, что
protected endpoint'ы возвращают 401 без Bearer-токена. Полный auth-flow
с реальной верификацией JWT — отдельный smoke-тест против Clerk-
sandbox'а; здесь нам достаточно убедиться, что middleware включён.

Public endpoint'ы (``/healthz``, ``/metrics``, ``/webhooks/clerk``)
остаются доступны.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def app_client_no_auth_override(postgres_dsn: str) -> AsyncIterator:
    """Test-client БЕЗ auth-override'а: дефолтный Clerk verify-флоу.

    Использует ``PARSER_SERVICE_CLERK_ISSUER`` пустым → Clerk-зависимости
    возвращают 503. Чтобы проверить именно 401-ветку, нужно дать
    непустой issuer (любой); тогда verify_clerk_jwt сорвётся на JWKS-
    fetch'е (мы не идём за реальным Clerk'ом) и middleware вернёт 401
    через ``_verify_signature``-fail. На самом деле без header'а вообще
    middleware ловит до verify, и возврат — 401 без обращения к JWKS.
    """
    import os

    saved_issuer = os.environ.get("PARSER_SERVICE_CLERK_ISSUER")
    os.environ["PARSER_SERVICE_CLERK_ISSUER"] = "https://test-401.clerk.local"
    os.environ["PARSER_SERVICE_DATABASE_URL"] = postgres_dsn

    try:
        from httpx import ASGITransport, AsyncClient

        # Снимаем все dependency_overrides, кроме arq pool — иначе
        # autouse-fixtures из conftest могут сделать «test-friendly»
        # auth и тест станет бессмысленным.
        from parser_service.auth import (
            get_clerk_settings,
            get_current_claims,
            get_current_claims_optional,
            get_current_user_id,
        )
        from parser_service.database import dispose_engine, init_engine
        from parser_service.main import app as production_app

        for dep in (
            get_clerk_settings,
            get_current_claims,
            get_current_claims_optional,
            get_current_user_id,
        ):
            production_app.dependency_overrides.pop(dep, None)

        init_engine(postgres_dsn)
        transport = ASGITransport(app=production_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
        await dispose_engine()
    finally:
        if saved_issuer is None:
            os.environ.pop("PARSER_SERVICE_CLERK_ISSUER", None)
        else:
            os.environ["PARSER_SERVICE_CLERK_ISSUER"] = saved_issuer


@pytest.mark.asyncio
async def test_healthz_is_public(app_client_no_auth_override) -> None:
    """healthz всегда доступен — liveness probe не должен падать на auth."""
    response = await app_client_no_auth_override.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_protected_endpoint_returns_401_without_bearer(
    app_client_no_auth_override,
) -> None:
    """``GET /trees/{id}/persons`` под auth-роутером — 401 без header'а."""
    import uuid

    response = await app_client_no_auth_override.get(f"/trees/{uuid.uuid4()}/persons")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.asyncio
async def test_protected_endpoint_returns_401_for_malformed_bearer(
    app_client_no_auth_override,
) -> None:
    """Неполный Bearer (без token-части) → 401."""
    import uuid

    response = await app_client_no_auth_override.get(
        f"/trees/{uuid.uuid4()}/persons",
        headers={"Authorization": "Bearer "},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_returns_401_for_unknown_scheme(
    app_client_no_auth_override,
) -> None:
    """Basic auth → 401 (не Bearer-схема)."""
    import uuid

    response = await app_client_no_auth_override.get(
        f"/trees/{uuid.uuid4()}/persons",
        headers={"Authorization": "Basic abcdef"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_clerk_webhook_endpoint_is_routed(app_client_no_auth_override) -> None:
    """``POST /webhooks/clerk`` без webhook-secret env → 503 (а не 404).

    Это проверка, что router-level auth НЕ применяется к webhook-у —
    он использует свою Svix-HMAC аутентификацию, не Bearer JWT.
    """
    response = await app_client_no_auth_override.post("/webhooks/clerk", json={})
    assert response.status_code in (
        # 503 если webhook secret пуст в env (наш случай).
        503,
        # 401 если каким-то образом dev-secret оказался выставлен.
        401,
    )
    assert response.status_code != 404
