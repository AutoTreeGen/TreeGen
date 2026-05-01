"""Тесты MCP-server handshake и регистрации tools/resources.

Не запускаем stdio полноценно (это требует subprocess + JSON-RPC), а
проверяем, что FastMCP-сервер собирается, объявляет ожидаемые tools/
resources, и его tools реально дёргают наш HTTP-клиент через
client_factory injection.
"""

from __future__ import annotations

import json

import httpx
import pytest
from pytest_httpx import HTTPXMock
from treegen_mcp.auth import (
    ApiCredentials,
    MissingApiKeyError,
    load_credentials,
)
from treegen_mcp.client import TreeGenClient
from treegen_mcp.config import TreeGenConfig, load_config
from treegen_mcp.server import build_server

EXPECTED_TOOLS = {
    "list_my_trees_tool",
    "get_tree_context_tool",
    "resolve_person_tool",
    "get_person_tool",
    "search_persons_tool",
}


@pytest.mark.asyncio
async def test_server_lists_all_tools(
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """list_tools() возвращает 5 ожидаемых tool'ов — это и есть handshake.

    MCP initialize-flow: host → server `tools/list` request → server
    отдаёт массив. FastMCP.list_tools() — внутренний эквивалент; его
    результат улетает по wire без изменений.
    """

    async def factory() -> TreeGenClient:
        return TreeGenClient(config=config, credentials=credentials)

    server = build_server(client_factory=factory)
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_server_call_tool_routes_to_http(
    httpx_mock: HTTPXMock,
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """Вызов через server.call_tool() пробрасывается до HTTP-клиента."""
    httpx_mock.add_response(
        method="GET",
        url=f"{config.api_url}/trees",
        json={"trees": [{"id": "t1", "name": "demo"}]},
        status_code=200,
    )

    # Фабрика возвращает client с инжектированным httpx.AsyncClient,
    # подцепленным к pytest-httpx.
    shared_http = httpx.AsyncClient()

    async def factory() -> TreeGenClient:
        return TreeGenClient(
            config=config,
            credentials=credentials,
            client=shared_http,
        )

    server = build_server(client_factory=factory)
    try:
        result = await server.call_tool("list_my_trees_tool", {})
    finally:
        await shared_http.aclose()

    # FastMCP оборачивает str-результат tool'а в TextContent;
    # извлекаем JSON-текст и парсим.
    text = _extract_text(result)
    payload = json.loads(text)
    assert payload == {"trees": [{"id": "t1", "name": "demo"}]}


@pytest.mark.asyncio
async def test_server_lists_resources(
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> None:
    """Resource templates треггёрятся: tree-context и person-card."""

    async def factory() -> TreeGenClient:
        return TreeGenClient(config=config, credentials=credentials)

    server = build_server(client_factory=factory)
    templates = await server.list_resource_templates()
    uris = {t.uriTemplate for t in templates}
    assert "treegen://trees/{tree_id}/context" in uris
    assert "treegen://persons/{person_id}" in uris


def test_load_credentials_missing_key_raises() -> None:
    """Без TREEGEN_API_KEY в env — MissingApiKeyError, понятная для юзера."""
    with pytest.raises(MissingApiKeyError):
        load_credentials(env={})


def test_load_credentials_strips_whitespace() -> None:
    """Пробелы вокруг ключа вырезаются (типичная ошибка copy-paste)."""
    creds = load_credentials(env={"TREEGEN_API_KEY": "  atg_xxx  "})
    assert creds.api_key == "atg_xxx"


def test_credentials_repr_does_not_leak_secret() -> None:
    """repr() не светит API-ключ — критично для логов и crash dumps."""
    creds = ApiCredentials(api_key="atg_super_secret_value")
    rendered = repr(creds)
    assert "super_secret_value" not in rendered
    # Префикс показываем для дебага окружения.
    assert "atg_" in rendered


def test_load_config_reads_env_with_defaults() -> None:
    """load_config читает TREEGEN_API_URL, дефолт — localhost."""
    cfg_default = load_config(env={})
    assert cfg_default.api_url == "http://localhost:8000"

    cfg_custom = load_config(env={"TREEGEN_API_URL": "https://api.prod/"})
    # Trailing slash должен срезаться, иначе вышло бы "https://api.prod//trees".
    assert cfg_custom.api_url == "https://api.prod"


def _extract_text(call_tool_result: object) -> str:
    """Достаёт первый text-фрагмент из FastMCP call_tool результата.

    FastMCP в разных версиях возвращает либо ``list[TextContent]``, либо
    ``tuple[list[TextContent], dict | None]`` (структурированный output
    добавлен в 1.10+). Поддерживаем оба варианта.
    """
    candidate: object = call_tool_result
    if isinstance(candidate, tuple) and len(candidate) >= 1:
        candidate = candidate[0]
    if isinstance(candidate, list) and candidate:
        first = candidate[0]
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text
    msg = f"unexpected call_tool result shape: {call_tool_result!r}"
    raise AssertionError(msg)
