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
