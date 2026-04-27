"""Liveness probe."""

from __future__ import annotations

import pytest


@pytest.mark.db
@pytest.mark.integration
async def test_healthz_returns_ok(app_client) -> None:
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
