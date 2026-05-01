"""MCP tool ``resolve_person`` — натурально-языковая ссылка → person ID.

Backend: ``POST /trees/{tree_id}/resolve-person`` (ego-resolver, ADR-0068).
Принимает фразу вида ``"my mother"``, ``"John Smith"``, ``"my father's
cousin"`` и возвращает :class:`PersonResolution` с confidence и
альтернативами.
"""

from __future__ import annotations

from typing import Any

from treegen_mcp.client import TreeGenClient


async def resolve_person(
    client: TreeGenClient,
    tree_id: str,
    reference: str,
    anchor_person_id: str | None = None,
) -> dict[str, Any]:
    """Разрешает natural-language ссылку в конкретного человека.

    Args:
        client: Авторизованный :class:`TreeGenClient`.
        tree_id: UUID дерева, в котором ищем.
        reference: Натуральная фраза (``"my mother"``, ``"John Smith"``,
            ``"GGGF on dad's side"``).
        anchor_person_id: От какой персоны считать relative-references.
            Без anchor — relative-фразы (``"my X"``) могут не разрешиться.

    Returns:
        JSON dict: ``{"person_id", "confidence", "alternatives", ...}``.
    """
    return await client.resolve_person(
        tree_id,
        reference,
        anchor_person_id=anchor_person_id,
    )
