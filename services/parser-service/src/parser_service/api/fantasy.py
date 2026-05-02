"""Fantasy filter HTTP API (Phase 5.10 / ADR-0077).

Endpoints:

* ``POST /trees/{tree_id}/fantasy-scan`` — выполнить scan и заменить active
  flags. Synchronous: для 30k-person дерева scan ≈ секунды на CPU.
* ``GET  /trees/{tree_id}/fantasy-flags`` — список flags под severity /
  dismissed фильтрами.
* ``POST /trees/{tree_id}/fantasy-flags/{flag_id}/dismiss`` — пометить
  как false-positive с reason.
* ``POST /trees/{tree_id}/fantasy-flags/{flag_id}/undismiss`` — отменить
  dismissal (вернуть flag в active).

**Brief specified async POST с 202.** v1 — sync POST с 200, потому что
in-process scan завершается за секунды для типичных деревьев. Async
deferred (см. ADR-0077 «Когда пересмотреть»).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from shared_models.orm import FantasyFlag as FantasyFlagOrm
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.services.fantasy_runner import (
    ScanSummary,
    execute_fantasy_scan,
)

router = APIRouter(tags=["fantasy"])


# ── Pydantic DTOs ────────────────────────────────────────────────────────────


class FantasyScanRequest(BaseModel):
    """Body для POST /fantasy-scan."""

    rules: list[str] | None = Field(
        default=None,
        description=(
            "Whitelist rule_id'ов; None или пусто — все default-enabled. "
            "Например ['birth_after_death', 'circular_descent']."
        ),
    )


class FantasyScanResponse(BaseModel):
    """Response для POST /fantasy-scan."""

    scan_id: uuid.UUID
    tree_id: uuid.UUID
    persons_scanned: int
    families_scanned: int
    flags_created: int
    flags_replaced: int
    by_severity: dict[str, int]


class FantasyFlagResponse(BaseModel):
    """Pydantic projection одной fantasy_flags row."""

    id: uuid.UUID
    tree_id: uuid.UUID
    subject_person_id: uuid.UUID | None
    subject_relationship_id: uuid.UUID | None
    rule_id: str
    severity: str
    confidence: float
    reason: str
    evidence_json: dict[str, Any]
    dismissed_at: dt.datetime | None
    dismissed_by: uuid.UUID | None
    dismissed_reason: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = ConfigDict(from_attributes=True)


class DismissRequest(BaseModel):
    """Body для POST /fantasy-flags/{id}/dismiss."""

    reason: str = Field(min_length=1, max_length=2000)


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/trees/{tree_id}/fantasy-scan",
    response_model=FantasyScanResponse,
    status_code=status.HTTP_200_OK,
    summary="Run fantasy filter scan and replace active flags.",
    description=(
        "Synchronous scan: loads tree from DB, runs all enabled rules, "
        "replaces all **active** (non-dismissed) flags for this tree. "
        "Dismissed flags are preserved.\n\n"
        "Brief specified 202 async; v1 is sync because scan completes in "
        "seconds for typical trees. Async mode deferred (ADR-0077)."
    ),
)
async def post_fantasy_scan(
    tree_id: uuid.UUID,
    body: FantasyScanRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FantasyScanResponse:
    """Execute scan."""
    enabled_rules: frozenset[str] | None = frozenset(body.rules) if body.rules else None
    summary: ScanSummary = await execute_fantasy_scan(
        session,
        tree_id,
        enabled_rules=enabled_rules,
    )
    await session.commit()
    return FantasyScanResponse(
        scan_id=summary.scan_id,
        tree_id=summary.tree_id,
        persons_scanned=summary.persons_scanned,
        families_scanned=summary.families_scanned,
        flags_created=summary.flags_created,
        flags_replaced=summary.flags_replaced,
        by_severity=summary.by_severity,
    )


@router.get(
    "/trees/{tree_id}/fantasy-flags",
    response_model=list[FantasyFlagResponse],
    summary="List fantasy flags for a tree.",
)
async def list_fantasy_flags(
    tree_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    severity: Annotated[
        Literal["info", "warning", "high", "critical"] | None,
        Query(description="Фильтр по severity (None = все уровни)."),
    ] = None,
    dismissed: Annotated[
        bool | None,
        Query(
            description=("true = только dismissed; false = только active; None (default) = все."),
        ),
    ] = None,
    rule_id: Annotated[
        str | None,
        Query(min_length=1, max_length=64, description="Фильтр по конкретному rule_id."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FantasyFlagResponse]:
    """Paginated list flags filterable по severity / dismissed / rule_id."""
    stmt = select(FantasyFlagOrm).where(FantasyFlagOrm.tree_id == tree_id)
    if severity is not None:
        stmt = stmt.where(FantasyFlagOrm.severity == severity)
    if rule_id is not None:
        stmt = stmt.where(FantasyFlagOrm.rule_id == rule_id)
    if dismissed is True:
        stmt = stmt.where(FantasyFlagOrm.dismissed_at.is_not(None))
    elif dismissed is False:
        stmt = stmt.where(FantasyFlagOrm.dismissed_at.is_(None))
    stmt = (
        stmt.order_by(
            FantasyFlagOrm.severity.desc(),
            FantasyFlagOrm.created_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [FantasyFlagResponse.model_validate(r) for r in rows]


@router.post(
    "/trees/{tree_id}/fantasy-flags/{flag_id}/dismiss",
    response_model=FantasyFlagResponse,
    summary="Dismiss a fantasy flag as false positive.",
)
async def dismiss_fantasy_flag(
    tree_id: uuid.UUID,
    flag_id: uuid.UUID,
    body: Annotated[DismissRequest, Body(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FantasyFlagResponse:
    """Set dismissed_at = now(), dismissed_reason = body.reason.

    No-op if уже dismissed: возвращаем текущее состояние без mutation
    (idempotent semantics).

    ``dismissed_by`` остаётся NULL в v1 — нет JIT user-resolution из
    Clerk claims здесь (parser-service делает это через JIT-create в
    другом месте). Phase 5.10b добавит правильный user-stamp.
    """
    flag = await _get_flag(session, tree_id, flag_id)
    if flag.dismissed_at is None:
        await session.execute(
            update(FantasyFlagOrm)
            .where(FantasyFlagOrm.id == flag_id)
            .values(
                dismissed_at=dt.datetime.now(tz=dt.UTC),
                dismissed_reason=body.reason,
            )
        )
        await session.commit()
        flag = await _get_flag(session, tree_id, flag_id)
    return FantasyFlagResponse.model_validate(flag)


@router.post(
    "/trees/{tree_id}/fantasy-flags/{flag_id}/undismiss",
    response_model=FantasyFlagResponse,
    summary="Undismiss a previously dismissed fantasy flag.",
)
async def undismiss_fantasy_flag(
    tree_id: uuid.UUID,
    flag_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FantasyFlagResponse:
    """Clear dismissed_at + dismissed_by + dismissed_reason одной транзакцией.

    No-op if уже active. Idempotent.
    """
    flag = await _get_flag(session, tree_id, flag_id)
    if flag.dismissed_at is not None:
        await session.execute(
            update(FantasyFlagOrm)
            .where(FantasyFlagOrm.id == flag_id)
            .values(
                dismissed_at=None,
                dismissed_by=None,
                dismissed_reason=None,
            )
        )
        await session.commit()
        flag = await _get_flag(session, tree_id, flag_id)
    return FantasyFlagResponse.model_validate(flag)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_flag(
    session: AsyncSession,
    tree_id: uuid.UUID,
    flag_id: uuid.UUID,
) -> FantasyFlagOrm:
    """404 if flag не существует ИЛИ принадлежит другому дереву."""
    stmt = select(FantasyFlagOrm).where(
        FantasyFlagOrm.id == flag_id,
        FantasyFlagOrm.tree_id == tree_id,
    )
    flag = (await session.execute(stmt)).scalar_one_or_none()
    if flag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"fantasy flag {flag_id} not found in tree {tree_id}",
        )
    return flag


__all__ = ["router"]
