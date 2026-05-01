"""MCP-сервер entry point.

Запускается через ``uv run treegen-mcp`` (см. ``pyproject.toml`` scripts).
Использует stdio-транспорт MCP — это стандарт для Claude Desktop /
большинства MCP-host'ов.

Архитектура:

* :func:`build_server` — конструирует :class:`FastMCP` инстанс,
  регистрирует tools/resources. Принимает ``ClientFactory`` для
  тестирования (передаём mock-клиент).
* :func:`main` — sync entry point для CLI: читает env, запускает stdio.
* :func:`run_async` — async вариант, для встраивания в чужой event loop.

Tool'ы и resource'ы открывают новый :class:`TreeGenClient` per-request.
Это нормально — в MCP-сценарии частота вызовов низкая (LLM-host
агрегирует), а connection-pooling httpx внутри одного процесса
переиспользует TCP через ``http2=False`` keepalive.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

from .auth import ApiCredentials, load_credentials
from .client import ApiError, TreeGenClient
from .config import TreeGenConfig, load_config
from .tools import (
    get_person,
    get_tree_context,
    list_my_trees,
    resolve_person,
    search_persons,
)

ClientFactory = Callable[[], Awaitable[TreeGenClient]]
"""Callable, возвращающий открытый TreeGenClient. Подменяется в тестах."""

SERVER_NAME = "treegen-mcp"


def _default_client_factory(
    config: TreeGenConfig,
    credentials: ApiCredentials,
) -> ClientFactory:
    """Фабрика, открывающая ``TreeGenClient`` per-request.

    Возвращаемый client не закрывается автоматически — caller
    обязан использовать ``async with`` через :func:`_with_client`.
    """

    async def _make() -> TreeGenClient:
        return TreeGenClient(config=config, credentials=credentials)

    return _make


async def _call(
    factory: ClientFactory,
    op: Callable[[TreeGenClient], Awaitable[dict[str, Any]]],
) -> str:
    """Открывает client, вызывает ``op``, возвращает JSON-текст.

    MCP-host (Claude Desktop) ожидает текстовые tool-result'ы; pretty-print
    JSON — самый удобный для LLM формат.
    """
    client = await factory()
    try:
        async with client:
            try:
                result = await op(client)
            except ApiError as exc:
                return json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False)
        return json.dumps(result, indent=2, ensure_ascii=False)
    finally:
        # Если __aenter__ не успел отработать — client всё равно был
        # сконструирован, но без owned httpx.AsyncClient внутри. Повторный
        # close не нужен, так как __aexit__ управляет owned-флагом.
        pass


def build_server(
    *,
    config: TreeGenConfig | None = None,
    credentials: ApiCredentials | None = None,
    client_factory: ClientFactory | None = None,
) -> FastMCP:
    """Конструирует FastMCP-сервер с зарегистрированными tools/resources.

    Args:
        config: Конфиг endpoint'а. ``None`` → читаем из env.
        credentials: API-ключ. ``None`` → читаем из env.
        client_factory: Фабрика TreeGenClient. ``None`` → дефолт. В тестах
            — функция, возвращающая mock-клиент. Если передана, ``config``
            и ``credentials`` могут быть ``None``.

    Returns:
        Готовый :class:`FastMCP`. Запустить через ``mcp.run()`` или
        ``mcp.run_stdio_async()``.
    """
    if client_factory is None:
        if config is None:
            config = load_config()
        if credentials is None:
            credentials = load_credentials()
        factory: ClientFactory = _default_client_factory(config, credentials)
    else:
        factory = client_factory

    mcp = FastMCP(SERVER_NAME)

    # ---- Tools --------------------------------------------------------

    @mcp.tool()
    async def list_my_trees_tool() -> str:
        """List the trees available to the authenticated user."""
        return await _call(factory, list_my_trees)

    @mcp.tool()
    async def get_tree_context_tool(
        tree_id: str,
        anchor_person_id: str | None = None,
    ) -> str:
        """Return a structured context-pack for a tree.

        Args:
            tree_id: UUID of the tree.
            anchor_person_id: Optional anchor for relative-references
                (e.g. so 'my mother' resolves correctly).
        """
        return await _call(
            factory,
            lambda c: get_tree_context(c, tree_id, anchor_person_id),
        )

    @mcp.tool()
    async def resolve_person_tool(
        tree_id: str,
        reference: str,
        anchor_person_id: str | None = None,
    ) -> str:
        """Resolve a natural-language reference (e.g. 'my mother', 'John Smith').

        Args:
            tree_id: UUID of the tree.
            reference: Natural-language phrase.
            anchor_person_id: Optional anchor for relative phrases.
        """
        return await _call(
            factory,
            lambda c: resolve_person(c, tree_id, reference, anchor_person_id),
        )

    @mcp.tool()
    async def get_person_tool(person_id: str) -> str:
        """Return the person card (names, dates, parents, spouses, children).

        Args:
            person_id: UUID of the person.
        """
        return await _call(factory, lambda c: get_person(c, person_id))

    @mcp.tool()
    async def search_persons_tool(tree_id: str, query: str) -> str:
        """Search persons in a tree by name (substring + phonetic).

        Args:
            tree_id: UUID of the tree.
            query: Name fragment (minimum 2 characters).
        """
        return await _call(factory, lambda c: search_persons(c, tree_id, query))

    # ---- Resources ----------------------------------------------------
    # treegen://trees/{tree_id}/context  → context-pack as a resource.
    # treegen://persons/{person_id}      → person card as a resource.

    @mcp.resource("treegen://trees/{tree_id}/context")
    async def tree_context_resource(tree_id: str) -> str:
        """Tree context-pack exposed as an MCP resource."""
        return await _call(factory, lambda c: get_tree_context(c, tree_id, None))

    @mcp.resource("treegen://persons/{person_id}")
    async def person_resource(person_id: str) -> str:
        """Person card exposed as an MCP resource."""
        return await _call(factory, lambda c: get_person(c, person_id))

    return mcp


async def run_async() -> None:
    """Async entry point — запускает сервер на stdio.

    Использовать, если caller сам управляет event loop'ом. Для CLI —
    см. :func:`main`.
    """
    server = build_server()
    await server.run_stdio_async()


def main() -> None:
    """Sync CLI entry point (см. ``[project.scripts]`` в pyproject).

    Читает env (``TREEGEN_API_URL``, ``TREEGEN_API_KEY``), строит сервер
    и запускает на stdio. Блокирующий вызов — возвращается только при
    закрытии stdio-канала (host-процесс Claude Desktop завершился).
    """
    server = build_server()
    server.run(transport="stdio")
