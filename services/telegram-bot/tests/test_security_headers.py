"""Phase 13.2 — security headers smoke на telegram-bot."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_security_headers_on_healthz() -> None:
    from telegram_bot.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_app_state_has_limiter() -> None:
    from telegram_bot.main import app

    assert app.state.limiter is not None
    assert app.state.service_name == "telegram-bot"
