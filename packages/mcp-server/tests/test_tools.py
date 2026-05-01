"""Тесты MCP-инструментов: проверяем, что каждый tool корректно
маппится на HTTP-вызов AutoTreeGen API gateway.

Стратегия: подменяем httpx.AsyncClient через pytest-httpx, дёргаем
tool-функцию, ассертим что:

* URL и метод совпадают с ожиданием;
* ``Authorization: Bearer <key>`` уехал в headers;
* body/params (для POST/GET-with-q) собраны корректно.
"""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock
from treegen_mcp.auth import ApiCredentials
from treegen_mcp.client import AuthError, NotFoundError, TreeGenClient
from treegen_mcp.config import TreeGenConfig
from treegen_mcp.tools import (
    get_person,
    get_tree_context,
    list_my_trees,
    resolve_person,
    search_persons,
)


@pytest.mark.asyncio
async def test_list_my_trees_calls_get_trees(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """``list_my_trees`` → GET /trees."""
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/trees",
        json={"trees": [{"id": "t1", "name": "Smith family"}]},
        status_code=200,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        result = await list_my_trees(api)

    assert result == {"trees": [{"id": "t1", "name": "Smith family"}]}
    sent = httpx_mock.get_request()
    assert sent is not None
    assert sent.headers["Authorization"] == f"Bearer {credentials.api_key}"


@pytest.mark.asyncio
async def test_get_tree_context_passes_anchor(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """``get_tree_context`` пробрасывает anchor_person_id как query param."""
    tree_id = "11111111-1111-1111-1111-111111111111"
    anchor_id = "22222222-2222-2222-2222-222222222222"
    httpx_mock.add_response(
        method="GET",
        url=(f"{config.api_url}/trees/{tree_id}/context?anchor_person_id={anchor_id}"),
        json={"persons": [], "anchor_person_id": anchor_id},
        status_code=200,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        result = await get_tree_context(api, tree_id, anchor_id)

    assert result["anchor_person_id"] == anchor_id


@pytest.mark.asyncio
async def test_get_tree_context_no_anchor_omits_param(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """Без anchor_person_id query-string пуст (а не "?anchor_person_id=None")."""
    tree_id = "11111111-1111-1111-1111-111111111111"
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/trees/{tree_id}/context",
        json={"persons": []},
        status_code=200,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        await get_tree_context(api, tree_id)

    sent = httpx_mock.get_request()
    assert sent is not None
    assert "anchor_person_id" not in sent.url.query.decode()


@pytest.mark.asyncio
async def test_resolve_person_posts_reference(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """``resolve_person`` → POST с body {reference, anchor_person_id?}."""
    tree_id = "11111111-1111-1111-1111-111111111111"
    httpx_mock.add_response(
        method="POST",
        url=f"{config.api_url}/trees/{tree_id}/resolve-person",
        json={"person_id": "p1", "confidence": 0.91, "alternatives": []},
        status_code=200,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        result = await resolve_person(api, tree_id, "my mother", "anchor-1")

    assert result["person_id"] == "p1"
    sent = httpx_mock.get_request()
    assert sent is not None
    import json as _json

    body = _json.loads(sent.content)
    assert body == {"reference": "my mother", "anchor_person_id": "anchor-1"}


@pytest.mark.asyncio
async def test_get_person_404_raises_not_found(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """404 от API gateway → NotFoundError из клиента (без retry)."""
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/persons/missing",
        status_code=404,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        with pytest.raises(NotFoundError):
            await get_person(api, "missing")


@pytest.mark.asyncio
async def test_search_persons_passes_query_param(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """``search_persons`` шлёт ``q=`` в query string."""
    tree_id = "11111111-1111-1111-1111-111111111111"
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/trees/{tree_id}/persons/search?q=Smith",
        json={"persons": [{"id": "p1", "display_name": "John Smith"}]},
        status_code=200,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        result = await search_persons(api, tree_id, "Smith")

    assert len(result["persons"]) == 1


@pytest.mark.asyncio
async def test_unauthorized_raises_auth_error(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """401/403 → AuthError, чтобы MCP-host показал понятный fail."""
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/trees",
        status_code=401,
    )

    async with httpx.AsyncClient() as http:
        api = TreeGenClient(config=config, credentials=credentials, client=http)
        with pytest.raises(AuthError):
            await list_my_trees(api)
