"""Tree change proposals — CRUD endpoints (Phase 15.4a).

* ``POST   /trees/{tree_id}/proposals``       — создать новый proposal.
* ``GET    /trees/{tree_id}/proposals``       — список (filter by status).
* ``GET    /proposals/{proposal_id}``         — single full record.

Phase 15.4b добавит approve / reject / evidence attach + permission
boundary differences (VIEWER vs EDITOR vs OWNER). Здесь — все три
endpoint'а доступны любому залогиненному user'у с access к дереву
(viewer+); пермишен на approve/merge будет в 15.4b.

Auto-evidence-population: при ``POST`` если ``tree.protected=True`` и
``protection_policy.require_evidence_for`` не пуст, проходим по
``proposal.diff.creates/updates`` и для каждого relationship-change
кладём ``EvidenceRequirement(relationship_id, kind)`` в
``evidence_required``. Это позволяет caller'у (UI) сразу увидеть, какие
источники надо приаттачить перед approve. См. ADR-0062 §«Evidence-required gate».
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import Tree, TreeChangeProposal, TreeMembership
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_gateway.auth import RequireUser
from api_gateway.database import get_session
from api_gateway.schemas import (
    EvidenceRequirement,
    ProposalCreate,
    ProposalListResponse,
    ProposalRead,
    ProposalStatus,
    ProtectionPolicy,
    RelationshipKind,
)

router = APIRouter()


async def _ensure_tree_access(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Tree:
    """Вернуть Tree, если у user'а есть доступ; иначе 404 (privacy-by-obscurity).

    Доступ = owner ИЛИ membership row. Возвращаем 404 (не 403) для tree'ев,
    к которым нет доступа — не палим существование чужих деревьев.
    """
    tree = await session.scalar(select(Tree).where(Tree.id == tree_id, Tree.deleted_at.is_(None)))
    if tree is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tree not found")

    if tree.owner_user_id == user_id:
        return tree

    is_member = await session.scalar(
        select(func.count(TreeMembership.id)).where(
            TreeMembership.tree_id == tree_id,
            TreeMembership.user_id == user_id,
        )
    )
    if not is_member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tree not found")
    return tree


def _auto_populate_evidence_required(
    *,
    diff: dict[str, Any],
    policy: ProtectionPolicy,
) -> list[dict[str, Any]]:
    """Прогуляться по diff'у и собрать EvidenceRequirement-список.

    Сейчас распознаём изменения с ``entity_type == "relationship"`` (или
    производные ``family_child``, ``spouse``) — для них caller обязан
    приаттачить source. Schema-конкретика — opaque jsonb caller'а;
    эвристика «kind определяется по ключу ``kind`` или ``relation_kind``
    в каждом change-record».
    """
    if not policy.require_evidence_for:
        return []

    required_kinds = set(policy.require_evidence_for)
    requirements: list[EvidenceRequirement] = []

    for section in ("creates", "updates"):
        for change in diff.get(section, []):
            if not isinstance(change, dict):
                continue
            kind_raw = change.get("kind") or change.get("relation_kind")
            if kind_raw not in required_kinds:
                continue
            relationship_id = (
                change.get("relationship_id") or change.get("entity_id") or change.get("id")
            )
            if not relationship_id:
                continue
            requirements.append(
                EvidenceRequirement(
                    relationship_id=str(relationship_id),
                    kind=kind_raw,
                )
            )
    return [r.model_dump() for r in requirements]


@router.post(
    "/trees/{tree_id}/proposals",
    response_model=ProposalRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tree change proposal",
)
async def create_proposal(
    tree_id: uuid.UUID,
    body: ProposalCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: RequireUser,
) -> TreeChangeProposal:
    """Создать ``open`` proposal для дерева.

    Auto-populates ``evidence_required`` если дерево protected и policy
    требует evidence для какого-то relationship kind в diff'е.
    """
    tree = await _ensure_tree_access(session, tree_id=tree_id, user_id=user_id)

    policy = ProtectionPolicy.model_validate(tree.protection_policy or {})
    diff_dump = body.diff.model_dump()
    evidence_required = (
        _auto_populate_evidence_required(diff=diff_dump, policy=policy) if tree.protected else []
    )

    proposal = TreeChangeProposal(
        tree_id=tree_id,
        author_user_id=user_id,
        title=body.title,
        summary=body.summary,
        diff=diff_dump,
        status="open",
        evidence_required=evidence_required,
    )
    session.add(proposal)
    await session.flush()
    return proposal


@router.get(
    "/trees/{tree_id}/proposals",
    response_model=ProposalListResponse,
    summary="List tree change proposals (filterable by status)",
)
async def list_proposals(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: RequireUser,
    status_filter: Annotated[
        ProposalStatus | None,
        Query(
            alias="status",
            description="Фильтр по state machine (open/approved/rejected/merged/rolled_back).",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProposalListResponse:
    """Список proposals для дерева."""
    await _ensure_tree_access(session, tree_id=tree_id, user_id=user_id)

    base_filters = [TreeChangeProposal.tree_id == tree_id]
    if status_filter is not None:
        base_filters.append(TreeChangeProposal.status == status_filter)

    total = await session.scalar(select(func.count(TreeChangeProposal.id)).where(*base_filters))

    rows = await session.execute(
        select(TreeChangeProposal)
        .where(*base_filters)
        .order_by(TreeChangeProposal.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    proposals = list(rows.scalars().all())

    return ProposalListResponse(
        tree_id=tree_id,
        total=int(total or 0),
        limit=limit,
        offset=offset,
        items=[ProposalRead.model_validate(p) for p in proposals],
    )


@router.get(
    "/proposals/{proposal_id}",
    response_model=ProposalRead,
    summary="Get a single proposal by id",
)
async def get_proposal(
    proposal_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    user_id: RequireUser,
) -> TreeChangeProposal:
    """Single proposal с полным diff payload."""
    proposal = await session.scalar(
        select(TreeChangeProposal).where(TreeChangeProposal.id == proposal_id)
    )
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Proposal not found")
    # Permission: tree access required (404 hides non-accessible).
    await _ensure_tree_access(session, tree_id=proposal.tree_id, user_id=user_id)
    return proposal


# Suppress unused-import in type-checked context — RelationshipKind exported
# для будущих 15.4b/c handlers, держим в публичном API схем.
_ = RelationshipKind
