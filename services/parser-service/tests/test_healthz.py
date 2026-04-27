"""Smoke-тесты на /healthz — запускаются без БД."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok() -> None:
    """``GET /healthz`` отвечает 200 ``{"status": "ok"}``."""
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Skip lifespan (нет БД для smoke-тестов).
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_cors_allows_localhost_3000() -> None:
    """CORS middleware пропускает GET-запросы с ``localhost:3000`` (web dev).

    Проверяем, что preflight (`OPTIONS`) и обычный GET возвращают
    ``Access-Control-Allow-Origin: http://localhost:3000``. Это invariant
    Phase 4.1 — без него next-dev и parser-service не могут общаться.
    """
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        preflight = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        actual = await client.get(
            "/healthz",
            headers={"Origin": "http://localhost:3000"},
        )

    assert preflight.status_code in (200, 204)
    assert preflight.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert actual.headers.get("access-control-allow-origin") == "http://localhost:3000"
