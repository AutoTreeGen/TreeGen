"""Тесты для FamilySearchClient.get_person и retry-логики.

Sample JSON ниже — упрощённая копия GEDCOM-X Person из публичной
документации FamilySearch
(https://developers.familysearch.org/docs/api/types/json/Person).
Реальные API-вызовы помечаются ``@pytest.mark.familysearch_real`` и
не входят в Phase 5.0 (нужен sandbox key).
"""

from __future__ import annotations

import pytest
from familysearch_client import (
    AuthError,
    ClientError,
    FamilySearchClient,
    FamilySearchConfig,
    FamilySearchError,
    FsGender,
    FsPerson,
    NotFoundError,
    RateLimitError,
    RetryPolicy,
    ServerError,
)
from pytest_httpx import HTTPXMock

# Реальный shape ответа /platform/tree/persons/{id} — обёртка persons[0].
SAMPLE_PERSON_RESPONSE: dict[str, object] = {
    "persons": [
        {
            "id": "KW7S-VQJ",
            "living": False,
            "gender": {"type": "http://gedcomx.org/Male"},
            "names": [
                {
                    "preferred": True,
                    "nameForms": [
                        {
                            "fullText": "John Quincy Smith",
                            "parts": [
                                {"type": "http://gedcomx.org/Given", "value": "John Quincy"},
                                {"type": "http://gedcomx.org/Surname", "value": "Smith"},
                            ],
                        }
                    ],
                },
                {
                    "preferred": False,
                    "nameForms": [{"fullText": "Johnny Smith"}],
                },
            ],
            "facts": [
                {
                    "type": "http://gedcomx.org/Birth",
                    "date": {"original": "3 Apr 1850"},
                    "place": {"original": "Boston, Massachusetts"},
                },
                {
                    "type": "http://gedcomx.org/Death",
                    "date": {"original": "12 Nov 1920"},
                },
            ],
        }
    ]
}


def _person_url(config: FamilySearchConfig, person_id: str) -> str:
    return f"{config.api_base_url}/platform/tree/persons/{person_id}"


# ---------------------------------------------------------------------------
# Construction / smoke
# ---------------------------------------------------------------------------


def test_client_imports_and_constructs() -> None:
    """FamilySearchClient конструируется с access_token и sandbox-дефолтом."""
    client = FamilySearchClient(access_token="test-token")
    assert client.config.environment == "sandbox"


def test_client_repr_does_not_leak_token() -> None:
    """repr() не содержит access_token."""
    client = FamilySearchClient(access_token="super-secret-token")
    assert "super-secret-token" not in repr(client)


@pytest.mark.asyncio
async def test_client_supports_async_context_manager() -> None:
    """async with … работает; на выходе owned client закрывается."""
    config = FamilySearchConfig.sandbox()
    async with FamilySearchClient(access_token="t", config=config) as client:
        assert client.config.environment == "sandbox"


def test_error_hierarchy() -> None:
    """Все специфичные ошибки наследуются от FamilySearchError."""
    for err_cls in (AuthError, NotFoundError, RateLimitError, ServerError, ClientError):
        assert issubclass(err_cls, FamilySearchError)


def test_rate_limit_error_carries_retry_after() -> None:
    """RateLimitError.retry_after доступен после конструирования."""
    err = RateLimitError("hit 429", retry_after=12.5)
    assert err.retry_after == 12.5
    assert "429" in str(err)


def test_fs_person_display_name_fallbacks_to_id() -> None:
    """Без имён display_name возвращает Person ID."""
    person = FsPerson(id="KW7S-VQJ", gender=FsGender.UNKNOWN)
    assert person.display_name == "KW7S-VQJ"


# ---------------------------------------------------------------------------
# get_person — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_person_returns_parsed_model(httpx_mock: HTTPXMock) -> None:
    """200 OK с GEDCOM-X JSON парсится в FsPerson."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "KW7S-VQJ"),
        json=SAMPLE_PERSON_RESPONSE,
        status_code=200,
    )

    async with FamilySearchClient(access_token="bearer-token", config=config) as fs:
        person = await fs.get_person("KW7S-VQJ")

    assert person.id == "KW7S-VQJ"
    assert person.gender == FsGender.MALE
    assert person.living is False
    assert person.display_name == "John Quincy Smith"  # preferred name picked

    # Names normalized (URI prefix stripped, parts mapped).
    preferred = person.names[0]
    assert preferred.preferred is True
    assert preferred.given == "John Quincy"
    assert preferred.surname == "Smith"

    # Facts normalized: prefix stripped from type, date/place taken from .original.
    types = {fact.type for fact in person.facts}
    assert types == {"Birth", "Death"}
    birth = next(f for f in person.facts if f.type == "Birth")
    assert birth.date_original == "3 Apr 1850"
    assert birth.place_original == "Boston, Massachusetts"


@pytest.mark.asyncio
async def test_get_person_sends_bearer_and_accept_headers(httpx_mock: HTTPXMock) -> None:
    """Запрос несёт Authorization: Bearer и Accept: application/x-fs-v1+json."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "KW7S-VQJ"),
        json=SAMPLE_PERSON_RESPONSE,
        status_code=200,
    )

    async with FamilySearchClient(access_token="bearer-xyz", config=config) as fs:
        await fs.get_person("KW7S-VQJ")

    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers["Authorization"] == "Bearer bearer-xyz"
    assert sent.headers["Accept"] == "application/x-fs-v1+json"


# ---------------------------------------------------------------------------
# get_person — error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_person_404_raises_not_found_error(httpx_mock: HTTPXMock) -> None:
    """404 → NotFoundError, без retry."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "MISSING"),
        status_code=404,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(NotFoundError):
            await fs.get_person("MISSING")


@pytest.mark.asyncio
async def test_get_person_401_raises_auth_error(httpx_mock: HTTPXMock) -> None:
    """401 → AuthError, без retry."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "KW7S-VQJ"),
        status_code=401,
    )

    async with FamilySearchClient(access_token="expired", config=config) as fs:
        with pytest.raises(AuthError):
            await fs.get_person("KW7S-VQJ")


@pytest.mark.asyncio
async def test_get_person_400_raises_client_error(httpx_mock: HTTPXMock) -> None:
    """400 (без invalid_grant — это про token endpoint) → ClientError."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "BAD"),
        status_code=400,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(ClientError):
            await fs.get_person("BAD")


# ---------------------------------------------------------------------------
# get_person — retry behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_person_429_then_200_succeeds_after_retry(httpx_mock: HTTPXMock) -> None:
    """429 → 200: tenacity делает повторную попытку, итог — успех."""
    config = FamilySearchConfig.sandbox()
    url = _person_url(config, "KW7S-VQJ")
    httpx_mock.add_response(method="GET", url=url, status_code=429, headers={"Retry-After": "0"})
    httpx_mock.add_response(method="GET", url=url, json=SAMPLE_PERSON_RESPONSE, status_code=200)

    fast_retry = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with FamilySearchClient(access_token="t", config=config, retry_policy=fast_retry) as fs:
        person = await fs.get_person("KW7S-VQJ")

    assert person.id == "KW7S-VQJ"
    # Подтверждаем, что было два HTTP-запроса (первый 429, второй 200).
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_get_person_503_then_200_succeeds_after_retry(httpx_mock: HTTPXMock) -> None:
    """5xx ServerError тоже retryable."""
    config = FamilySearchConfig.sandbox()
    url = _person_url(config, "KW7S-VQJ")
    httpx_mock.add_response(method="GET", url=url, status_code=503)
    httpx_mock.add_response(method="GET", url=url, json=SAMPLE_PERSON_RESPONSE, status_code=200)

    fast_retry = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with FamilySearchClient(access_token="t", config=config, retry_policy=fast_retry) as fs:
        person = await fs.get_person("KW7S-VQJ")

    assert person.id == "KW7S-VQJ"
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_get_person_429_exhausted_raises_rate_limit_error(httpx_mock: HTTPXMock) -> None:
    """3 подряд 429 — поднимается RateLimitError с retry_after."""
    config = FamilySearchConfig.sandbox()
    url = _person_url(config, "KW7S-VQJ")
    for _ in range(3):
        httpx_mock.add_response(
            method="GET", url=url, status_code=429, headers={"Retry-After": "7"}
        )

    fast_retry = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with FamilySearchClient(access_token="t", config=config, retry_policy=fast_retry) as fs:
        with pytest.raises(RateLimitError) as excinfo:
            await fs.get_person("KW7S-VQJ")

    assert excinfo.value.retry_after == 7.0
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.asyncio
async def test_get_person_404_does_not_retry(httpx_mock: HTTPXMock) -> None:
    """404 — не-retryable, должен быть ровно один HTTP-запрос."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "GHOST"),
        status_code=404,
    )

    fast_retry = RetryPolicy(max_attempts=5, initial_wait=0.0, max_wait=0.0)
    async with FamilySearchClient(access_token="t", config=config, retry_policy=fast_retry) as fs:
        with pytest.raises(NotFoundError):
            await fs.get_person("GHOST")
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_get_person_requires_async_context_manager() -> None:
    """Без async with клиент должен поднимать RuntimeError, а не ловить None."""
    client = FamilySearchClient(access_token="t")
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.get_person("KW7S-VQJ")


# ---------------------------------------------------------------------------
# Mapping edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_person_handles_minimal_payload(httpx_mock: HTTPXMock) -> None:
    """Person без gender/names/facts парсится в FsPerson с дефолтами."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "MIN"),
        json={"persons": [{"id": "MIN"}]},
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        person = await fs.get_person("MIN")

    assert person.id == "MIN"
    assert person.gender == FsGender.UNKNOWN
    assert person.names == ()
    assert person.facts == ()
    assert person.living is None
    assert person.display_name == "MIN"  # fallback to id


@pytest.mark.asyncio
async def test_get_person_handles_empty_persons_array(httpx_mock: HTTPXMock) -> None:
    """200 с пустым persons[] — это не наш случай 404, но защищаемся ValueError."""
    config = FamilySearchConfig.sandbox()
    httpx_mock.add_response(
        method="GET",
        url=_person_url(config, "X"),
        json={"persons": []},
        status_code=200,
    )

    async with FamilySearchClient(access_token="t", config=config) as fs:
        with pytest.raises(ValueError, match="empty 'persons' array"):
            await fs.get_person("X")
