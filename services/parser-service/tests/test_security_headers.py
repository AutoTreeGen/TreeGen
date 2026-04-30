"""Phase 13.2 — security headers + CORS smoke на parser-service.

Защищаемся от drift'а: если когда-нибудь main.py забудет вызвать
``apply_security_middleware`` или вернёт старый CORS-конфиг с
``allow_credentials=False``, эти тесты упадут.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_security_headers_on_healthz() -> None:
    """Все ожидаемые headers — на ``/healthz``."""
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-origin" in r.headers["referrer-policy"]
    assert "camera=()" in r.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_cors_preflight_allows_localhost_and_credentials() -> None:
    """CORS preflight для ``http://localhost:3000`` пропускает credentials."""
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/healthz",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert r.status_code in (200, 204)
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    # Phase 13.2 (ADR-0053): allow_credentials=True для Bearer-tokens.
    assert r.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_cors_rejects_unknown_origin() -> None:
    """Origin не из whitelist → headers не выставляются (CORS-блок).

    Starlette CORSMiddleware при non-allowed origin не отдаёт
    ``Access-Control-Allow-Origin`` — браузер тогда блокирует ответ.
    """
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers


@pytest.mark.asyncio
async def test_app_state_has_limiter() -> None:
    """``app.state.limiter`` доступен сторонним ручкам для tighter-тарифа."""
    from parser_service.main import app

    assert app.state.limiter is not None
    assert app.state.service_name == "parser-service"
