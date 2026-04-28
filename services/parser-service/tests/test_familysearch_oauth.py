"""Тесты server-side OAuth flow для FamilySearch (Phase 5.1, ADR-0027).

Покрывают:

* token-crypto round-trip (без БД).
* state-store: save/consume через fakeredis (atomic getdel).
* GET /imports/familysearch/oauth/start → 200 + cookie + state в Redis.
* GET /imports/familysearch/oauth/callback — happy path + CSRF mismatch +
  expired state + FS token-exchange ошибка.
* DELETE /imports/familysearch/disconnect — обнуляет колонку, идемпотентно.
* GET /imports/familysearch/me — статус подключения.

FS-сторона мокается через httpx (auth.complete_flow / users/current).
Redis — fakeredis с подменой module-level фабрики в api/familysearch.py.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from familysearch_client import AuthError, Token
from shared_models.orm import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Token crypto — pure-unit, без БД.
# ---------------------------------------------------------------------------


def test_token_crypto_roundtrip() -> None:
    """encrypt → decrypt возвращает равный по полям объект."""
    from parser_service.fs_oauth import (
        FsStoredToken,
        get_token_storage,
    )

    key = Fernet.generate_key().decode("ascii")
    storage = get_token_storage(key)

    token = FsStoredToken(
        access_token="atk",
        refresh_token="rtk",
        expires_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        scope="openid profile",
        fs_user_id="MMMM-MMM",
        stored_at=dt.datetime(2026, 4, 28, tzinfo=dt.UTC),
    )

    ciphertext = storage.encrypt(token)
    assert isinstance(ciphertext, str)
    assert ciphertext != token.access_token

    restored = storage.decrypt(ciphertext)
    assert restored == token


def test_token_crypto_wrong_key_raises() -> None:
    """Чтение ciphertext'а чужим ключом → TokenCryptoError, не InvalidToken."""
    from parser_service.fs_oauth import (
        FsStoredToken,
        TokenCryptoError,
        get_token_storage,
    )

    key_a = Fernet.generate_key().decode("ascii")
    key_b = Fernet.generate_key().decode("ascii")

    storage_a = get_token_storage(key_a)
    storage_b = get_token_storage(key_b)

    token = FsStoredToken(
        access_token="atk",
        refresh_token=None,
        expires_at=dt.datetime(2030, 1, 1, tzinfo=dt.UTC),
        scope=None,
        fs_user_id=None,
        stored_at=dt.datetime(2026, 4, 28, tzinfo=dt.UTC),
    )
    ciphertext = storage_a.encrypt(token)
    with pytest.raises(TokenCryptoError):
        storage_b.decrypt(ciphertext)


def test_is_fs_token_storage_configured_validates_key_format() -> None:
    """Пустая / битая ENV-переменная → False, нормальный ключ → True."""
    from parser_service.fs_oauth import is_fs_token_storage_configured

    assert is_fs_token_storage_configured("") is False
    assert is_fs_token_storage_configured("not-a-fernet-key") is False
    assert is_fs_token_storage_configured(Fernet.generate_key().decode("ascii")) is True


# ---------------------------------------------------------------------------
# State store — через fakeredis.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis() -> Any:
    """Async fakeredis-клиент с собственным in-memory сервером."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    redis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    yield redis
    await redis.aclose()


@pytest.mark.asyncio
async def test_state_store_save_and_consume_roundtrip(fake_redis: Any) -> None:
    """save_state → consume_state возвращает тот же record и удаляет ключ."""
    from parser_service.fs_oauth import (
        OAuthStateRecord,
        consume_state,
        save_state,
    )

    record = OAuthStateRecord(
        state="state-abc",
        code_verifier="cv-xyz",
        user_id=uuid.uuid4(),
        redirect_uri="http://localhost:8000/cb",
        scope="profile",
    )
    await save_state(fake_redis, record, ttl_seconds=60)

    consumed = await consume_state(fake_redis, "state-abc")
    assert consumed == record

    # Второй consume → None (атомарное удаление).
    second = await consume_state(fake_redis, "state-abc")
    assert second is None


@pytest.mark.asyncio
async def test_state_store_unknown_state_returns_none(fake_redis: Any) -> None:
    """consume_state на несуществующий ключ → None."""
    from parser_service.fs_oauth import consume_state

    assert await consume_state(fake_redis, "never-existed") is None


# ---------------------------------------------------------------------------
# HTTP-эндпоинты OAuth.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fs_env(monkeypatch: pytest.MonkeyPatch, fake_redis: Any) -> dict[str, str]:
    """Подставить ENV для FS OAuth + замокать Redis-фабрику в api.familysearch."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("PARSER_SERVICE_FS_TOKEN_KEY", key)
    monkeypatch.setenv("PARSER_SERVICE_FS_CLIENT_ID", "test-client-id")
    monkeypatch.setenv(
        "PARSER_SERVICE_FS_OAUTH_REDIRECT_URI",
        "http://localhost:8000/imports/familysearch/oauth/callback",
    )
    monkeypatch.setenv(
        "PARSER_SERVICE_FS_FRONTEND_SUCCESS_URL",
        "http://localhost:3000/familysearch/connect?status=ok",
    )
    monkeypatch.setenv(
        "PARSER_SERVICE_FS_FRONTEND_FAILURE_URL",
        "http://localhost:3000/familysearch/connect?status=error",
    )

    # get_settings кешировать не должен (BaseSettings пере-читывает env при
    # инстанцировании), но на всякий — сбросить lru_cache, если будет.
    from parser_service.config import get_settings

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()  # type: ignore[attr-defined]

    # Подменяем module-level redis-фабрику на ту, что отдаст наш fakeredis.
    from parser_service.api import familysearch as fs_api

    monkeypatch.setattr(fs_api, "_redis_client_factory", lambda: fake_redis)
    # _make_redis_client будет вызывать .aclose() — fakeredis это поддерживает,
    # но так как клиент шарится между запросами теста, патчим aclose в no-op
    # для этого клиента, чтобы тест мог переиспользовать инстанс.
    fake_redis.aclose = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return {"key": key}


@pytest.mark.asyncio
async def test_oauth_start_returns_authorize_url_and_sets_cookie(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — фикстура только для side-effect (ENV + redis-фабрика)
    fake_redis: Any,
) -> None:
    """GET /oauth/start → 200 + authorize_url + cookie + Redis-state."""
    response = await app_client.get("/imports/familysearch/oauth/start")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["authorize_url"].startswith("https://identbeta.familysearch.org/")
    assert body["state"]
    assert body["expires_in"] > 0

    # Cookie выставлен.
    set_cookie = response.headers.get("set-cookie") or ""
    assert "fs_oauth_state" in set_cookie

    # State лежит в fakeredis.
    raw = await fake_redis.get(f"fs:oauth:state:{body['state']}")
    assert raw is not None
    payload = json.loads(raw)
    assert payload["state"] == body["state"]
    assert payload["code_verifier"]


@pytest.mark.asyncio
async def test_oauth_start_503_when_token_key_missing(
    app_client,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без PARSER_SERVICE_FS_TOKEN_KEY → 503 c понятным detail."""
    monkeypatch.delenv("PARSER_SERVICE_FS_TOKEN_KEY", raising=False)
    response = await app_client.get("/imports/familysearch/oauth/start")
    assert response.status_code == 503
    assert "FS_TOKEN_KEY" in response.json()["detail"]


@pytest.mark.asyncio
async def test_oauth_callback_state_mismatch_redirects_to_failure(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
) -> None:
    """Cookie не совпадает с query-state → redirect на failure URL."""
    response = await app_client.get(
        "/imports/familysearch/oauth/callback",
        params={"code": "abc", "state": "real-state"},
        cookies={"fs_oauth_state": "different-state"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "status=error" in response.headers["location"]
    assert "reason=state_mismatch" in response.headers["location"]


@pytest.mark.asyncio
async def test_oauth_callback_missing_params_redirects_to_failure(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
) -> None:
    """Без code/state → redirect на failure URL с reason=missing_params."""
    response = await app_client.get(
        "/imports/familysearch/oauth/callback",
        cookies={"fs_oauth_state": "ignored"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "reason=missing_params" in response.headers["location"]


@pytest.mark.asyncio
async def test_oauth_callback_user_declined_redirects_to_failure(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
) -> None:
    """?error=access_denied → redirect с reason=declined."""
    response = await app_client.get(
        "/imports/familysearch/oauth/callback",
        params={"error": "access_denied"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "reason=declined" in response.headers["location"]


@pytest.mark.asyncio
async def test_oauth_callback_happy_path_stores_encrypted_token(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],
    fake_redis: Any,  # noqa: ARG001 — side-effect: подменяет redis-фабрику в API
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный happy-path: code+state → token exchange → row.fs_token_encrypted."""
    # 1) Сначала запросим start, чтобы получить state и положить запись в Redis.
    start_response = await app_client.get("/imports/familysearch/oauth/start")
    assert start_response.status_code == 200, start_response.text
    state = start_response.json()["state"]

    # 2) Подменяем FamilySearchAuth.complete_flow на возврат фиктивного Token.
    fake_token = Token(
        access_token="fake-access",
        refresh_token="fake-refresh",
        expires_in=3600,
        scope="openid",
    )

    async def fake_complete_flow(self: Any, **kwargs: Any) -> Token:  # noqa: ARG001
        return fake_token

    monkeypatch.setattr(
        "parser_service.api.familysearch.FamilySearchAuth.complete_flow",
        fake_complete_flow,
    )

    # 3) FS users/current — патчим напрямую, чтобы не задеть httpx, который
    # использует сам TestClient (ASGI-транспорт). Возвращаем None — fs_user_id
    # необязателен, callback должен пройти и без него.
    async def fake_fetch_fs_user_id(_token: str, _settings: Any) -> str | None:
        return None

    monkeypatch.setattr(
        "parser_service.api.familysearch._fetch_fs_user_id",
        fake_fetch_fs_user_id,
    )

    # 4) Сам callback.
    response = await app_client.get(
        "/imports/familysearch/oauth/callback",
        params={"code": "auth-code", "state": state},
        cookies={"fs_oauth_state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302, response.text
    assert "status=ok" in response.headers["location"]

    # 5) В БД лежит зашифрованный токен; расшифровка совпадает с fake_token.access_token.
    from parser_service.fs_oauth import get_token_storage

    storage = get_token_storage(fs_env["key"])
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            res = await session.execute(select(User).where(User.email == "owner@autotreegen.local"))
            user = res.scalar_one()
            assert user.fs_token_encrypted is not None
            decoded = storage.decrypt(user.fs_token_encrypted)
            assert decoded.access_token == "fake-access"
            assert decoded.refresh_token == "fake-refresh"
            # Токен ещё свежий: expires_at > now.
            assert not decoded.is_expired()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_oauth_callback_token_exchange_failure_redirects(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FS отверг code → AuthError → redirect c reason=token_exchange_failed."""
    start_response = await app_client.get("/imports/familysearch/oauth/start")
    state = start_response.json()["state"]

    async def fake_complete_flow(self: Any, **kwargs: Any) -> Token:  # noqa: ARG001
        msg = "invalid_grant"
        raise AuthError(msg)

    monkeypatch.setattr(
        "parser_service.api.familysearch.FamilySearchAuth.complete_flow",
        fake_complete_flow,
    )

    response = await app_client.get(
        "/imports/familysearch/oauth/callback",
        params={"code": "x", "state": state},
        cookies={"fs_oauth_state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "reason=token_exchange_failed" in response.headers["location"]


# ---------------------------------------------------------------------------
# /me + /disconnect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_me_returns_disconnected_when_no_token(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
    postgres_dsn: str,  # noqa: ARG001 — нужен для миграций (testcontainers)
) -> None:
    """Без сохранённого токена → connected=False."""
    # Сначала disconnect, чтобы быть уверенными в чистом состоянии (тесты
    # делят один Postgres внутри сессии).
    await app_client.delete("/imports/familysearch/disconnect")

    response = await app_client.get("/imports/familysearch/me")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connected"] is False
    assert body["fs_user_id"] is None


@pytest.mark.asyncio
async def test_disconnect_is_idempotent(
    app_client,  # type: ignore[no-untyped-def]
    fs_env: dict[str, str],  # noqa: ARG001 — side-effect фикстура
) -> None:
    """DELETE /disconnect возвращает 204 даже если токена не было."""
    response = await app_client.delete("/imports/familysearch/disconnect")
    assert response.status_code == 204
    response2 = await app_client.delete("/imports/familysearch/disconnect")
    assert response2.status_code == 204


# ---------------------------------------------------------------------------
# Sanity: отсутствует client_id → 503.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_start_503_when_client_id_missing(
    app_client,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Если ENV PARSER_SERVICE_FS_CLIENT_ID пустой — 503 (не 500)."""
    monkeypatch.setenv("PARSER_SERVICE_FS_TOKEN_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("PARSER_SERVICE_FS_CLIENT_ID", "")
    response = await app_client.get("/imports/familysearch/oauth/start")
    assert response.status_code == 503
    assert "CLIENT_ID" in response.json()["detail"]
