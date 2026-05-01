"""Ego-relationship resolver (Phase 10.7a / ADR-0068).

Pure-function модуль: даёт владельцу дерева вычислить родство «от себя» к
любой персоне (брат жены / тёща / прапрадед). Базовая фундаментальная
зависимость для Phase 10.7b Context Pack serializer, 10.7d Chat UI и
10.8 MCP server identity API.

Без anchor'а (``trees.owner_person_id``) и без эго-резолвера AI-фичи путают
«брата жены» с «братом тёщи» — это исходный pain-point владельца, который
phase 10.7a решает на уровне фундамента.

Пакет — pure-functions: никаких sqlalchemy / fastapi / httpx (см. ADR-0016
§«Pure functions без I/O»). Caller (api-gateway) загружает данные дерева
в ``FamilyTraversal`` и передаёт в ``relate()``.
"""

from inference_engine.ego_relations.humanize import humanize
from inference_engine.ego_relations.resolver import (
    NoPathError,
    relate,
)
from inference_engine.ego_relations.types import (
    FamilyNode,
    FamilyTraversal,
    RelationshipPath,
)

__all__ = [
    "FamilyNode",
    "FamilyTraversal",
    "NoPathError",
    "RelationshipPath",
    "humanize",
    "relate",
]
