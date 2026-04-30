"""Health check smoke."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.db, pytest.mark.integration]


@pytest.mark.asyncio
async def test_healthz_returns_ok(app_client) -> None:
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
