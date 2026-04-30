"""Unit-тесты FamilySearch адаптера (Phase 9.0 / ADR-0055).

httpx-моки через ``pytest-httpx`` — НЕ дёргает живой FamilySearch.
Маркер ``familysearch_real`` (skipped в CI) зарезервирован для тестов
с настоящими credentials, но в этом файле их нет.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from archive_service.adapters.familysearch import (
    AdapterRateLimitError,
    FamilySearchAdapter,
    _hash_params,
    make_fs_config,
)
from archive_service.config import Settings, get_settings
from familysearch_client import AuthError, NotFoundError
from pytest_httpx import HTTPXMock


def _make_adapter(redis_fake: Any, http_client: httpx.AsyncClient) -> FamilySearchAdapter:
    settings = get_settings()
    return FamilySearchAdapter(
        settings=settings,
        redis=redis_fake,
        http_client=http_client,
    )


SEARCH_BODY_OK = {
    "entries": [
        {
            "id": "1:1:ABC-123",
            "title": "Ivan Petrov, b. 1850",
            "summary": "Birth record, Pinsk",
            "score": 0.92,
            "content": {
                "gedcomx": {
                    "persons": [
                        {
                            "id": "p1",
                            "names": [
                                {"nameForms": [{"fullText": "Ivan Petrov"}]},
                            ],
                        },
                    ],
                },
            },
        },
    ],
}

SEARCH_BODY_EMPTY: dict[str, Any] = {"entries": []}

PERSON_BODY_OK = {
    "persons": [
        {
            "id": "KW7S-VQJ",
            "names": [{"nameForms": [{"fullText": "John Doe"}]}],
            "gender": {"type": "http://gedcomx.org/Male"},
            "facts": [{"type": "http://gedcomx.org/Birth", "date": {"original": "1820"}}],
        },
    ],
}


@pytest.mark.asyncio
async def test_search_records_success(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        url="http://test/platform/records/search?q=surname%3A%22Petrov%22&count=20",
        method="GET",
        json=SEARCH_BODY_OK,
        headers={"ETag": 'W/"abc123"'},
    )
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        hits = await adapter.search_records(
            access_token="fake_access",
            user_id="user_1",
            surname="Petrov",
        )
    assert len(hits) == 1
    assert hits[0].title == "Ivan Petrov, b. 1850"
    assert hits[0].score == pytest.approx(0.92)
    # ETag сохранился в кэше.
    cache_key = _hash_params(
        "/platform/records/search",
        {"q": 'surname:"Petrov"', "count": "20"},
    )
    cached = await patch_redis_factory.hgetall(f"fs:cache:{cache_key}")
    assert cached["etag"] == 'W/"abc123"'


@pytest.mark.asyncio
async def test_search_records_zero_results(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=SEARCH_BODY_EMPTY, headers={"ETag": '"empty"'})
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        hits = await adapter.search_records(
            access_token="fake_access",
            user_id="user_1",
            given="NoSuchName",
        )
    assert hits == []


@pytest.mark.asyncio
async def test_search_uses_etag_returns_cached_on_304(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    # Pre-fill cache.
    cache_key = _hash_params(
        "/platform/records/search",
        {"q": 'surname:"Cached"', "count": "20"},
    )
    await patch_redis_factory.hset(
        f"fs:cache:{cache_key}",
        mapping={"etag": '"old"', "body": json.dumps(SEARCH_BODY_OK)},
    )
    # FS responds 304.
    httpx_mock.add_response(status_code=304)
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        hits = await adapter.search_records(
            access_token="fake_access",
            user_id="user_1",
            surname="Cached",
        )
    assert len(hits) == 1
    # Verify If-None-Match was sent.
    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers.get("If-None-Match") == '"old"'


@pytest.mark.asyncio
async def test_get_person_404(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(status_code=404, json={"errors": ["not found"]})
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        with pytest.raises(NotFoundError):
            await adapter.get_person(
                access_token="fake_access",
                user_id="user_1",
                fsid="ZZZZ-ZZZ",
            )


@pytest.mark.asyncio
async def test_get_person_success(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=PERSON_BODY_OK, headers={"ETag": '"p1"'})
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        detail = await adapter.get_person(
            access_token="fake_access",
            user_id="user_1",
            fsid="KW7S-VQJ",
        )
    assert detail.fsid == "KW7S-VQJ"
    assert detail.full_name == "John Doe"
    assert detail.gender == "http://gedcomx.org/Male"
    assert len(detail.facts) == 1


@pytest.mark.asyncio
async def test_local_rate_limit_blocks_after_capacity(
    monkeypatch: pytest.MonkeyPatch,
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    """Bucket capacity = 2; третий запрос должен упасть в AdapterRateLimitError."""
    monkeypatch.setenv("ARCHIVE_SERVICE_FS_RATE_LIMIT_BURST", "2")
    monkeypatch.setenv("ARCHIVE_SERVICE_FS_RATE_LIMIT_PER_HOUR", "1")  # медленный refill
    httpx_mock.add_response(json=SEARCH_BODY_EMPTY, headers={"ETag": '"e"'})
    httpx_mock.add_response(json=SEARCH_BODY_EMPTY, headers={"ETag": '"e2"'})
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        await adapter.search_records(
            access_token="t",
            user_id="user_burst",
            given="A",
        )
        await adapter.search_records(
            access_token="t",
            user_id="user_burst",
            given="B",
        )
        with pytest.raises(AdapterRateLimitError) as exc_info:
            await adapter.search_records(
                access_token="t",
                user_id="user_burst",
                given="C",
            )
    assert exc_info.value.retry_after is not None


@pytest.mark.asyncio
async def test_oauth_state_save_and_consume(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
) -> None:
    adapter = FamilySearchAdapter(settings=get_settings(), redis=patch_redis_factory)
    request = adapter.start_authorize(redirect_uri="http://test/callback")
    await adapter.save_oauth_state(request)
    # Должен быть найден ровно один раз (GETDEL).
    verifier = await adapter.consume_oauth_state(request.state)
    assert verifier == request.code_verifier
    # Повторный consume — None (уже потреблён).
    again = await adapter.consume_oauth_state(request.state)
    assert again is None


@pytest.mark.asyncio
async def test_refresh_calls_fs_token_endpoint(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    """``refresh()`` → POST на token endpoint sandbox-конфига."""
    httpx_mock.add_response(
        method="POST",
        url="https://identbeta.familysearch.org/cis-web/oauth2/v3/token",
        json={
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 3600,
            "scope": "openid",
        },
    )
    async with httpx.AsyncClient() as client:
        adapter = FamilySearchAdapter(
            settings=get_settings(),
            redis=patch_redis_factory,
            http_client=client,
        )
        token = await adapter.refresh(refresh_token="r1")
    assert token.access_token == "new_access"
    assert token.refresh_token == "new_refresh"
    assert token.expires_in == 3600


@pytest.mark.asyncio
async def test_search_401_raises_auth_error(
    fs_env: None,  # noqa: ARG001 — side-effect-only фикстура (ENV).
    patch_redis_factory: Any,
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(status_code=401, json={"error": "expired"})
    async with httpx.AsyncClient() as client:
        adapter = _make_adapter(patch_redis_factory, client)
        with pytest.raises(AuthError):
            await adapter.search_records(
                access_token="bad",
                user_id="user_1",
                surname="X",
            )


def test_make_fs_config_sandbox_for_test_url() -> None:
    """``http://test`` (тестовый base_url) → sandbox-конфиг с подменённым api_base_url."""
    config = make_fs_config("http://test")
    assert config.environment == "sandbox"
    assert config.api_base_url == "http://test"


def test_make_fs_config_production_for_prod_url() -> None:
    config = make_fs_config("https://api.familysearch.org")
    assert config.environment == "production"


def test_settings_loads_familysearch_global_envs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAMILYSEARCH_CLIENT_ID", "abc")
    monkeypatch.setenv("FAMILYSEARCH_REDIRECT_URI", "http://r/cb")
    monkeypatch.setenv("FAMILYSEARCH_BASE_URL", "https://api.familysearch.org")
    settings = Settings()
    assert settings.familysearch_client_id == "abc"
    assert settings.familysearch_redirect_uri == "http://r/cb"
    assert settings.familysearch_base_url == "https://api.familysearch.org"
