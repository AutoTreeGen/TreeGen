"""Healthz probe + privacy meta-fields."""

from __future__ import annotations

import pytest


@pytest.mark.db
@pytest.mark.integration
async def test_healthz_returns_ok(app_client) -> None:
    response = await app_client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    # Critical for prod alerting (per ADR-0020): test fixture sets
    # require_encryption=False, so this should be False here. In prod
    # it must be True.
    assert payload["dna_encryption_required"] is False
