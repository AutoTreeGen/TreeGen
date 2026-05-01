"""MCP tool ``get_tree_context`` — context-pack для LLM.

Backend: ``GET /trees/{tree_id}/context[?anchor_person_id=...]``.
Context-pack — структурированный снимок дерева (persons, families,
ego-anchor, recent edits, top hypotheses) в формате, который LLM
может потреблять напрямую.
"""

from __future__ import annotations

from typing import Any

from treegen_mcp.client import TreeGenClient


async def get_tree_context(
    client: TreeGenClient,
    tree_id: str,
    anchor_person_id: str | None = None,
) -> dict[str, Any]:
    """Возвращает context-pack для дерева.

    Args:
        client: Авторизованный :class:`TreeGenClient`.
        tree_id: UUID дерева.
        anchor_person_id: Опциональный фокус-перс для relative-references.
            Если задан — LLM может разрешать ``"my mother"`` в ID персоны.

    Returns:
        JSON dict с context-pack'ом (форма определяется API gateway).
    """
    return await client.get_tree_context(tree_id, anchor_person_id=anchor_person_id)
