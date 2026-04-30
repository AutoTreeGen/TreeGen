"""Smoke-тест healthz."""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_healthz_returns_ok(app_client: object) -> None:
    response = await app_client.get("/healthz")  # type: ignore[attr-defined]
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
