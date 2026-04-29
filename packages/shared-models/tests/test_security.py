"""Phase 13.2 — unit-тесты ``shared_models.security``.

Покрывают:

* CORS-origin parsing из env (default + comma-separated + пустые элементы).
* Security-headers middleware: nosniff/X-Frame-Options/Referrer-Policy/Permissions-Policy
  присутствуют, HSTS только на https-scheme.
* MaxBodySizeMiddleware: 413 для Content-Length > limit;
  ``/imports/*`` — расширенный лимит.
* apply_security_middleware: устанавливает ``app.state.limiter``,
  rate-limit срабатывает после превышения тарифа.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from shared_models.security import (
    MaxBodySizeMiddleware,
    SecurityHeadersMiddleware,
    _parse_origins,
    apply_security_middleware,
)


@pytest.fixture
def _clear_cors_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CORS_ORIGINS", raising=False)


# ---------------------------------------------------------------------------
# _parse_origins
# ---------------------------------------------------------------------------


def test_parse_origins_default() -> None:
    """Без env — local-dev fallback."""
    assert _parse_origins(None) == ["http://localhost:3000"]
    assert _parse_origins("") == ["http://localhost:3000"]


def test_parse_origins_comma_separated() -> None:
    """Comma-separated env разбирается, лишние пробелы и пустые элементы отрезаются."""
    raw = "https://app.example.com, https://staging.example.com, "
    assert _parse_origins(raw) == [
        "https://app.example.com",
        "https://staging.example.com",
    ]


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware
# ---------------------------------------------------------------------------


def _build_app_with_security() -> FastAPI:
    """Минимальный FastAPI с одним ручкой и full-стеком security middleware."""
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    apply_security_middleware(app, service_name="test-svc")
    return app


@pytest.mark.asyncio
async def test_security_headers_present_on_http() -> None:
    """X-Content-Type-Options / X-Frame-Options / Referrer-Policy / Permissions-Policy
    — на каждом ответе. HSTS — только https.
    """
    app = _build_app_with_security()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ping")
    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["permissions-policy"]
    # http → no HSTS
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_hsts_added_on_https_scheme() -> None:
    """ASGI-scope с scheme=https → middleware добавляет HSTS."""
    captured: dict[str, Any] = {}

    async def app_inner(scope: Any, _receive: Any, send: Any) -> None:
        async def send_wrapper(message: Any) -> None:
            captured.setdefault("messages", []).append(message)
            await send(message)

        if scope["type"] == "http":
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                },
            )
            await send_wrapper({"type": "http.response.body", "body": b"ok"})

    middleware = SecurityHeadersMiddleware(app_inner)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "scheme": "https",
        "method": "GET",
        "path": "/",
        "headers": [],
    }
    await middleware(scope, receive, send)

    start = next(m for m in sent if m["type"] == "http.response.start")
    keys = {k for k, _ in start["headers"]}
    assert b"strict-transport-security" in keys
    assert b"x-content-type-options" in keys


# ---------------------------------------------------------------------------
# MaxBodySizeMiddleware
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_body_size_default_limit_rejects_oversized() -> None:
    """Content-Length > 1 МБ — 413 на стандартной ручке."""
    app = _build_app_with_security()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 2 MiB declared content-length — middleware не дочитает body.
        response = await client.post(
            "/ping",
            content=b"x" * 16,  # реальные body короткие, важен только header
            headers={"content-length": str(2 * 1024 * 1024)},
        )
    assert response.status_code == 413
    assert "too large" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_body_size_imports_path_uses_extended_limit() -> None:
    """``/imports/*`` — лимит 200 МБ, 50 МБ запрос проходит middleware."""

    async def captured_app(_scope: Any, _receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            },
        )
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = MaxBodySizeMiddleware(captured_app)

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    # 50 МБ Content-Length на /imports/* — не должен резать.
    scope = {
        "type": "http",
        "scheme": "http",
        "method": "POST",
        "path": "/imports/foo",
        "headers": [(b"content-length", str(50 * 1024 * 1024).encode())],
    }
    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 200


# ---------------------------------------------------------------------------
# apply_security_middleware
# ---------------------------------------------------------------------------


def test_apply_security_middleware_attaches_state() -> None:
    """``app.state.limiter`` и ``app.state.service_name`` — выставлены."""
    app = FastAPI()
    apply_security_middleware(app, service_name="parser-test")
    assert app.state.service_name == "parser-test"
    assert app.state.limiter is not None  # slowapi.Limiter, точный тип не важен.


@pytest.mark.asyncio
async def test_rate_limit_kicks_in_after_default_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4-й запрос на тарифе ``3/minute`` → 429.

    Глобальный conftest отключает rate-limit (``RATE_LIMITING_ENABLED=false``),
    чтобы межтестовое состояние не отравляло сервисы; здесь мы явно
    включаем его обратно для smoke'а самой логики.
    """
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "true")

    app = FastAPI()
    apply_security_middleware(app, service_name="rate-test", default_rate_limit="3/minute")

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        statuses = []
        for _ in range(5):
            r = await client.get("/ping")
            statuses.append(r.status_code)

    # Ровно 3 проходят, 2 получают 429.
    assert statuses.count(200) == 3
    assert statuses.count(429) == 2
