"""Тесты WikimediaCommonsClient.

Sample JSON ниже — упрощённая копия реального Action API ответа на

    GET commons.wikimedia.org/w/api.php?action=query&generator=geosearch&...&prop=imageinfo&iiprop=url|extmetadata&...

Реальные API-вызовы помечаются ``@pytest.mark.commons_real`` и не
входят в Phase 9.1 (тестируются вручную при изменении API).
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from wikimedia_commons_client import (
    Attribution,
    ClientError,
    CommonsImage,
    License,
    NotFoundError,
    RateLimitError,
    RetryPolicy,
    ServerError,
    WikimediaCommonsClient,
    WikimediaCommonsConfig,
    WikimediaCommonsError,
)

API_URL = "https://commons.wikimedia.org/w/api.php"

# Регэксп под URL с query-string'ом — pytest-httpx требует точного
# url-матчинга, а у нас десятки query-параметров; матчим по host+path.
URL_RE = re.compile(re.escape(API_URL) + r"\?.*")


def _geosearch_response(
    *,
    image_url: str = "https://upload.wikimedia.org/test.jpg",
    page_url: str = "https://commons.wikimedia.org/wiki/File:Test.jpg",
    thumb_url: str | None = "https://upload.wikimedia.org/thumb/test.jpg",
    license_short: str | None = "CC BY-SA 4.0",
    credit: str | None = "<a href='https://example.org'>Example Author</a>",
    attribution_required: str | None = "true",
    title: str = "File:Test.jpg",
    width: int = 1024,
    height: int = 768,
) -> dict[str, Any]:
    """Builds a Commons-shaped JSON response for one image."""
    extmeta: dict[str, Any] = {}
    if license_short is not None:
        extmeta["LicenseShortName"] = {"value": license_short}
        extmeta["LicenseUrl"] = {"value": "https://creativecommons.org/licenses/by-sa/4.0"}
    if credit is not None:
        extmeta["Credit"] = {"value": credit}
    if attribution_required is not None:
        extmeta["AttributionRequired"] = {"value": attribution_required}

    imageinfo: dict[str, Any] = {
        "url": image_url,
        "descriptionurl": page_url,
        "width": width,
        "height": height,
        "mime": "image/jpeg",
        "extmetadata": extmeta,
    }
    if thumb_url is not None:
        imageinfo["thumburl"] = thumb_url
    return {
        "query": {
            "pages": [
                {
                    "pageid": 12345,
                    "title": title,
                    "imageinfo": [imageinfo],
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Construction / config / repr
# ---------------------------------------------------------------------------


def test_client_constructs_with_defaults() -> None:
    """WikimediaCommonsClient конструируется без аргументов."""
    client = WikimediaCommonsClient()
    assert client.config.api_url == API_URL
    assert "AutoTreeGen" in client.config.user_agent


def test_config_rejects_empty_user_agent() -> None:
    """WikimediaCommonsConfig() с пустым UA → ValueError (WMF policy)."""
    with pytest.raises(ValueError, match="user_agent"):
        WikimediaCommonsConfig(user_agent="")
    with pytest.raises(ValueError, match="user_agent"):
        WikimediaCommonsConfig(user_agent="   ")


def test_client_repr_contains_api_url() -> None:
    client = WikimediaCommonsClient()
    assert API_URL in repr(client)


def test_error_hierarchy() -> None:
    for err_cls in (NotFoundError, RateLimitError, ServerError, ClientError):
        assert issubclass(err_cls, WikimediaCommonsError)


def test_rate_limit_error_carries_retry_after() -> None:
    err = RateLimitError("hit 429", retry_after=12.5)
    assert err.retry_after == 12.5


@pytest.mark.asyncio
async def test_async_context_manager() -> None:
    """async with … работает; на выходе owned client закрывается."""
    async with WikimediaCommonsClient() as client:
        assert client.config.api_url == API_URL


@pytest.mark.asyncio
async def test_call_without_context_manager_raises() -> None:
    """Без async with клиент не должен делать запросов."""
    client = WikimediaCommonsClient()
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.search_by_coordinates(latitude=0.0, longitude=0.0)


# ---------------------------------------------------------------------------
# search_by_coordinates — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_coordinates_returns_parsed_image(httpx_mock: HTTPXMock) -> None:
    """Geosearch 200 → CommonsImage с full attribution."""
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())

    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=54.687, longitude=25.279)

    assert len(results) == 1
    img = results[0]
    assert isinstance(img, CommonsImage)
    assert img.title == "File:Test.jpg"
    assert str(img.image_url) == "https://upload.wikimedia.org/test.jpg"
    assert str(img.page_url) == "https://commons.wikimedia.org/wiki/File:Test.jpg"
    assert img.thumb_url is not None
    assert img.width == 1024
    assert img.height == 768
    assert img.mime == "image/jpeg"

    assert isinstance(img.license, License)
    assert img.license.short_name == "CC BY-SA 4.0"
    assert img.license.url is not None

    assert isinstance(img.attribution, Attribution)
    assert img.attribution.credit_html is not None
    assert "Example Author" in img.attribution.credit_html
    assert img.attribution.required is True


@pytest.mark.asyncio
async def test_search_by_coordinates_sends_user_agent_header(httpx_mock: HTTPXMock) -> None:
    """User-Agent header идёт в каждом запросе (WMF policy)."""
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())
    config = WikimediaCommonsConfig(user_agent="MyApp/1.0 (test@example.org)")

    async with WikimediaCommonsClient(config=config) as commons:
        await commons.search_by_coordinates(latitude=0.0, longitude=0.0)

    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers["User-Agent"] == "MyApp/1.0 (test@example.org)"


@pytest.mark.asyncio
async def test_search_by_coordinates_includes_required_params(httpx_mock: HTTPXMock) -> None:
    """Geosearch URL содержит generator/координаты/iiprop."""
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())

    async with WikimediaCommonsClient() as commons:
        await commons.search_by_coordinates(
            latitude=54.687, longitude=25.279, radius_m=2500, limit=20
        )

    sent = httpx_mock.get_request()
    assert sent is not None
    qs = sent.url.params
    assert qs["action"] == "query"
    assert qs["generator"] == "geosearch"
    assert qs["ggscoord"] == "54.687|25.279"
    assert qs["ggsradius"] == "2500"
    assert qs["ggslimit"] == "20"
    assert qs["ggsnamespace"] == "6"
    assert "extmetadata" in qs["iiprop"]


# ---------------------------------------------------------------------------
# search_by_coordinates — empty / partial responses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_returns_empty_list_when_no_pages(httpx_mock: HTTPXMock) -> None:
    """Geosearch 200 без query.pages → пустой список (не ошибка)."""
    httpx_mock.add_response(method="GET", url=URL_RE, json={"batchcomplete": True})

    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert results == []


@pytest.mark.asyncio
async def test_search_skips_missing_pages(httpx_mock: HTTPXMock) -> None:
    """Page с missing=true пропускается."""
    payload = {
        "query": {
            "pages": [
                {"title": "File:Ghost.jpg", "missing": True},
                _geosearch_response()["query"]["pages"][0],
            ]
        }
    }
    httpx_mock.add_response(method="GET", url=URL_RE, json=payload)

    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(results) == 1
    assert results[0].title == "File:Test.jpg"


@pytest.mark.asyncio
async def test_search_skips_pages_without_imageinfo(httpx_mock: HTTPXMock) -> None:
    """Page без imageinfo (broken record) — пропускается, не валит выдачу."""
    payload = {
        "query": {
            "pages": [
                {"title": "File:Broken.jpg"},  # no imageinfo
                _geosearch_response()["query"]["pages"][0],
            ]
        }
    }
    httpx_mock.add_response(method="GET", url=URL_RE, json=payload)

    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(results) == 1


# ---------------------------------------------------------------------------
# License / Attribution edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_image_without_license_short_name_has_none(httpx_mock: HTTPXMock) -> None:
    """Файл без LicenseShortName → CommonsImage.license = None (parsing tolerant)."""
    httpx_mock.add_response(
        method="GET",
        url=URL_RE,
        json=_geosearch_response(license_short=None),
    )

    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(results) == 1
    assert results[0].license is None


@pytest.mark.asyncio
async def test_attribution_required_false_parses_to_false(httpx_mock: HTTPXMock) -> None:
    """``AttributionRequired: "false"`` → Attribution.required = False."""
    httpx_mock.add_response(
        method="GET", url=URL_RE, json=_geosearch_response(attribution_required="false")
    )
    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert results[0].attribution.required is False


@pytest.mark.asyncio
async def test_attribution_required_missing_defaults_true(httpx_mock: HTTPXMock) -> None:
    """Отсутствующий AttributionRequired трактуется как True (safer для license-compliance)."""
    httpx_mock.add_response(
        method="GET", url=URL_RE, json=_geosearch_response(attribution_required=None)
    )
    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert results[0].attribution.required is True


@pytest.mark.asyncio
async def test_thumb_url_optional(httpx_mock: HTTPXMock) -> None:
    """Если Commons не отдал thumburl, CommonsImage.thumb_url = None."""
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response(thumb_url=None))
    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert results[0].thumb_url is None


# ---------------------------------------------------------------------------
# search_by_title
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_title_uses_search_generator(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())
    async with WikimediaCommonsClient() as commons:
        results = await commons.search_by_title(query="Vilnius synagogue")

    assert len(results) == 1
    sent = httpx_mock.get_request()
    assert sent is not None
    qs = sent.url.params
    assert qs["generator"] == "search"
    assert qs["gsrsearch"] == "Vilnius synagogue"
    assert qs["gsrnamespace"] == "6"


def test_search_by_title_rejects_empty_query() -> None:
    """Пустой query — ValueError ещё до HTTP."""
    import asyncio

    async def _go() -> None:
        async with WikimediaCommonsClient() as commons:
            await commons.search_by_title(query="   ")

    with pytest.raises(ValueError, match="query must be non-empty"):
        asyncio.run(_go())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_radius", [-1, 0, 9, 10_001, 999_999])
@pytest.mark.asyncio
async def test_search_by_coordinates_rejects_bad_radius(bad_radius: int) -> None:
    async with WikimediaCommonsClient() as commons:
        with pytest.raises(ValueError, match="radius_m"):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0, radius_m=bad_radius)


@pytest.mark.parametrize("bad_limit", [0, -1, 501, 10_000])
@pytest.mark.asyncio
async def test_search_by_coordinates_rejects_bad_limit(bad_limit: int) -> None:
    async with WikimediaCommonsClient() as commons:
        with pytest.raises(ValueError, match="limit"):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0, limit=bad_limit)


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_404_raises_not_found(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=URL_RE, status_code=404)
    async with WikimediaCommonsClient() as commons:
        with pytest.raises(NotFoundError):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0)


@pytest.mark.asyncio
async def test_search_403_raises_client_error(httpx_mock: HTTPXMock) -> None:
    """403 (UA-policy violation) → ClientError, без retry."""
    httpx_mock.add_response(method="GET", url=URL_RE, status_code=403)
    fast = RetryPolicy(max_attempts=5, initial_wait=0.0, max_wait=0.0)
    async with WikimediaCommonsClient(retry_policy=fast) as commons:
        with pytest.raises(ClientError):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    # 403 — non-retryable, ровно один request
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.asyncio
async def test_search_mediawiki_error_payload_raises_client_error(
    httpx_mock: HTTPXMock,
) -> None:
    """200 OK + error-объект в теле → ClientError (не retryable)."""
    httpx_mock.add_response(
        method="GET",
        url=URL_RE,
        json={"error": {"code": "badparams", "info": "Invalid coordinate"}},
    )
    async with WikimediaCommonsClient() as commons:
        with pytest.raises(ClientError, match="Invalid coordinate"):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0)


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_429_then_200_succeeds_after_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=URL_RE, status_code=429, headers={"Retry-After": "0"})
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())

    fast = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with WikimediaCommonsClient(retry_policy=fast) as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(results) == 1
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_503_then_200_succeeds_after_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=URL_RE, status_code=503)
    httpx_mock.add_response(method="GET", url=URL_RE, json=_geosearch_response())

    fast = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with WikimediaCommonsClient(retry_policy=fast) as commons:
        results = await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(results) == 1
    assert len(httpx_mock.get_requests()) == 2


@pytest.mark.asyncio
async def test_429_exhausted_raises_rate_limit_error(httpx_mock: HTTPXMock) -> None:
    for _ in range(3):
        httpx_mock.add_response(
            method="GET", url=URL_RE, status_code=429, headers={"Retry-After": "5"}
        )
    fast = RetryPolicy(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    async with WikimediaCommonsClient(retry_policy=fast) as commons:
        with pytest.raises(RateLimitError) as excinfo:
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert excinfo.value.retry_after == 5.0
    assert len(httpx_mock.get_requests()) == 3


@pytest.mark.asyncio
async def test_404_does_not_retry(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(method="GET", url=URL_RE, status_code=404)
    fast = RetryPolicy(max_attempts=5, initial_wait=0.0, max_wait=0.0)
    async with WikimediaCommonsClient(retry_policy=fast) as commons:
        with pytest.raises(NotFoundError):
            await commons.search_by_coordinates(latitude=0.0, longitude=0.0)
    assert len(httpx_mock.get_requests()) == 1
