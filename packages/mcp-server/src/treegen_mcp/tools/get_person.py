"""MCP tool ``get_person`` — карточка персоны по ID.

Backend: ``GET /persons/{person_id}`` (parser-service ``trees.py``,
существующий endpoint). Возвращает имена, gender, факты, родителей,
супругов, детей.
"""

from __future__ import annotations

from typing import Any

from treegen_mcp.client import TreeGenClient


async def get_person(client: TreeGenClient, person_id: str) -> dict[str, Any]:
    """Возвращает карточку персоны.

    Args:
        client: Авторизованный :class:`TreeGenClient`.
        person_id: UUID персоны.

    Returns:
        JSON dict с ``PersonDetail`` (см. parser-service schemas).
    """
    return await client.get_person(person_id)
