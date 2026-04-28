"""Tests for /healthz."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_returns_200_with_flags(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["bot_configured"] is True
    assert body["webhook_secret_configured"] is True
