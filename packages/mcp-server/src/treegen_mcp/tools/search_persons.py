"""MCP tool ``search_persons`` — name-search в дереве.

Backend: ``GET /trees/{tree_id}/persons/search?q={query}`` (parser-service
``trees.py``, существующий endpoint). Поддерживает substring и phonetic
matching, плюс фильтрацию по диапазону годов рождения (через API
напрямую — здесь не пробрасываем для простоты v1).
"""

from __future__ import annotations

from typing import Any

from treegen_mcp.client import TreeGenClient


async def search_persons(
    client: TreeGenClient,
    tree_id: str,
    query: str,
) -> dict[str, Any]:
    """Ищет персоны в дереве по имени.

    Args:
        client: Авторизованный :class:`TreeGenClient`.
        tree_id: UUID дерева.
        query: Подстрока имени (минимум 2 символа — gateway сам валидирует).

    Returns:
        JSON dict: ``{"persons": [...], "next_cursor": "..."}``.
    """
    return await client.search_persons(tree_id, query)
