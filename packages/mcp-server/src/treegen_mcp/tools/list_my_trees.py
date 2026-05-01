"""MCP tool ``list_my_trees`` — список деревьев пользователя.

Backend: ``GET /trees`` на AutoTreeGen API. Возвращает то, что вернул
gateway (минимум — ``{"trees": [...]}``).
"""

from __future__ import annotations

from typing import Any

from treegen_mcp.client import TreeGenClient


async def list_my_trees(client: TreeGenClient) -> dict[str, Any]:
    """Возвращает список деревьев, доступных пользователю.

    Args:
        client: Авторизованный :class:`TreeGenClient`.

    Returns:
        JSON dict: ``{"trees": [{"id", "name", "person_count", ...}, ...]}``.
    """
    return await client.list_trees()
