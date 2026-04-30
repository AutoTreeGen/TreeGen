"""End-to-end FastAPI тесты на TestClient (httpx ASGITransport).

Авторизация Clerk закрыта overrides на ``app.dependency_overrides``,
аналогично паттерну dna-service. ``RATE_LIMITING_ENABLED=false``
включён глобально через autouse-фикстуру в conftest.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from familysearch_client import Token
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_healthz_ok_without_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Healthz всегда отдаёт 200 + флаги конфигурации."""
    # Удаляем все relevant ENV — fresh process.
    for key in (
        "FAMILYSEARCH_CLIENT_ID",
        "FAMILYSEARCH_REDIRECT_URI",
        "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    from archive_service.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["familysearch_configured"] is False
    assert body["token_storage_configured"] is False


@pytest.mark.asyncio
async def test_search_503_when_fs_envs_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без ``FAMILYSEARCH_CLIENT_ID`` — endpoint отдаёт 503."""
    monkeypatch.delenv("FAMILYSEARCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("FAMILYSEARCH_REDIRECT_URI", raising=False)
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.main import app

    # Стабим Clerk-зависимости — иначе они отдадут 401 раньше нашего 503.
    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/search",
                params={"q": "X"},
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 503
        assert "FAMILYSEARCH_CLIENT_ID" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_200_with_mocked_adapter(
    monkeypatch: pytest.MonkeyPatch,
    redis_fake: Any,
) -> None:
    """Все ENV выставлены, адаптер замокан — 200 + JSON ``{hits: [...]}``."""
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")
    monkeypatch.setenv(
        "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")

    from archive_service.adapters.familysearch import RecordHit
    from archive_service.api.familysearch import (
        get_adapter,
        get_redis,
        get_token_storage,
    )
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.config import get_settings
    from archive_service.main import app
    from archive_service.token_storage import TokenStorage

    # Адаптер — AsyncMock с заранее заданным результатом search_records.
    fake_adapter = AsyncMock()
    fake_adapter.search_records.return_value = [
        RecordHit(fsid="1:1:Z", title="Sample", summary=None, score=0.5, persons=[]),
    ]

    # TokenStorage — реальный, но содержит сохранённый токен (через redis_fake).
    settings = get_settings()
    storage = TokenStorage(fernet_key=settings.token_encryption_key)
    await storage.save(
        redis_fake,
        user_id="user_x",
        token=Token(
            access_token="fake_access",
            refresh_token=None,
            expires_in=3600,
            scope=None,
        ),
    )

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    app.dependency_overrides[get_adapter] = lambda: fake_adapter
    app.dependency_overrides[get_redis] = lambda: redis_fake
    app.dependency_overrides[get_token_storage] = lambda: storage
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/search",
                params={"surname": "Petrov"},
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["hits"][0]["title"] == "Sample"
        fake_adapter.search_records.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oauth_start_returns_pkce_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    redis_fake: Any,
) -> None:
    """``/oauth/start`` отдаёт authorize_url + state + code_verifier."""
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.api.familysearch import get_redis
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.main import app

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    app.dependency_overrides[get_redis] = lambda: redis_fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/oauth/start",
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["authorize_url"].startswith("http")
        assert body["state"]
        assert body["code_verifier"]
        # code_verifier ушёл в Redis под state-ключом.
        stored = await redis_fake.get(f"fs:oauth_state:{body['state']}")
        assert stored == body["code_verifier"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oauth_callback_400_on_unknown_state(
    monkeypatch: pytest.MonkeyPatch,
    redis_fake: Any,
) -> None:
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")
    monkeypatch.setenv(
        "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.api.familysearch import get_redis
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.main import app

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    app.dependency_overrides[get_redis] = lambda: redis_fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/oauth/callback",
                params={"code": "c", "state": "never_saved"},
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 400
        assert "expired" in r.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_person_endpoint_404_and_401_mapping(
    monkeypatch: pytest.MonkeyPatch,
    redis_fake: Any,
) -> None:
    """Адаптер бросает NotFoundError → 404; AuthError → 401."""
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")
    monkeypatch.setenv(
        "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.api.familysearch import (
        get_adapter,
        get_redis,
        get_token_storage,
    )
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.config import get_settings
    from archive_service.main import app
    from archive_service.token_storage import TokenStorage
    from familysearch_client import AuthError, NotFoundError

    settings = get_settings()
    storage = TokenStorage(fernet_key=settings.token_encryption_key)
    await storage.save(
        redis_fake,
        user_id="user_x",
        token=Token(
            access_token="fake_access",
            refresh_token=None,
            expires_in=3600,
            scope=None,
        ),
    )

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    app.dependency_overrides[get_redis] = lambda: redis_fake
    app.dependency_overrides[get_token_storage] = lambda: storage

    fake_adapter_404 = AsyncMock()
    fake_adapter_404.get_person.side_effect = NotFoundError("nope")
    app.dependency_overrides[get_adapter] = lambda: fake_adapter_404
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/person/AAAA-AAA",
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 404

        # Перепроверяем ту же ручку с AuthError.
        fake_adapter_401 = AsyncMock()
        fake_adapter_401.get_person.side_effect = AuthError("expired")
        app.dependency_overrides[get_adapter] = lambda: fake_adapter_401
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/person/AAAA-AAA",
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_search_401_when_no_saved_token(
    monkeypatch: pytest.MonkeyPatch,
    redis_fake: Any,
) -> None:
    """Если в Redis нет сохранённого токена — 401."""
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "http://test")
    monkeypatch.setenv(
        "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.api.familysearch import get_redis
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.main import app

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    app.dependency_overrides[get_redis] = lambda: redis_fake
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/search",
                params={"q": "X"},
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 401
        assert "FamilySearch token" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_oauth_callback_503_without_encryption_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "fs_app")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://test/cb")
    monkeypatch.delenv("ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ARCHIVE_SERVICE_CLERK_ISSUER", "https://clerk.test")
    from archive_service.auth import get_current_claims, get_current_user_id
    from archive_service.main import app

    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(sub="user_x")
    app.dependency_overrides[get_current_user_id] = lambda: "user_x"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                "/archives/familysearch/oauth/callback",
                params={"code": "c", "state": "s"},
                headers={"Authorization": "Bearer fake"},
            )
        assert r.status_code == 503
        assert "TOKEN_ENCRYPTION_KEY" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
