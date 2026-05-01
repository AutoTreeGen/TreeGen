"""Safe Merge endpoint (Phase 5.7b).

Принимает ``DiffReport`` (из 5.7a) и ``MergePolicy``, применяет diff к
target-дереву через ``gedcom_parser.merge.apply_diff_to_session``. Атомарно:
``missing_anchor`` => HTTP 200 + ``aborted=true`` + пустой ``applied``.
Soft-конфликты разрешаются по policy.

.. note:: Service location
   Brief Phase 5.7b просил эндпоинт в ``services/api-gateway/...``, но
   ``api-gateway`` в текущей main — пустой placeholder (только ``.gitkeep``);
   все HTTP-роуты живут в ``parser-service``. Следуем существующей
   конвенции (см. также ADR-разговоры в брифе Phase 5.5b о
   ``/api/v1/gedcom/`` префиксах).

Permission: EDITOR — merge мутирует tree, viewer'ом такое делать нельзя;
требовать OWNER — слишком жёстко, EDITOR — стандартный уровень для
mutation'ов в ADR-0036.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from gedcom_parser.merge import (
    DiffReport,
    MergePolicy,
    MergeResult,
    apply_diff_to_session,
)
from pydantic import BaseModel, ConfigDict, Field
from shared_models import TreeRole
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.database import get_session
from parser_service.services.permissions import require_tree_role

router = APIRouter()


class SafeMergeRequest(BaseModel):
    """Тело ``POST /api/v1/trees/{tree_id}/merge``.

    ``diff_report`` — то, что произвела 5.7a (либо стаб-сериализация из
    UI). ``policy`` — стратегия конфликтов; default ``manual`` означает,
    что любой field-overlap попадёт в ``skipped`` без записи (дальше UI
    Phase 5.7c проведёт review).
    """

    model_config = ConfigDict(extra="forbid")

    diff_report: DiffReport
    policy: MergePolicy = Field(default_factory=MergePolicy)


@router.post(
    "/api/v1/trees/{tree_id}/merge",
    response_model=MergeResult,
    summary="Apply a GEDCOM diff to a tree atomically with conflict-aware resolution.",
    description=(
        "Phase 5.7b — Safe Merge applier. Atomic: либо все изменения "
        "применяются (внутри SQL-savepoint'а), либо при наличии "
        "missing_anchor — ни одно изменение не материализуется и в "
        "ответе ``aborted=true``."
    ),
    dependencies=[Depends(require_tree_role(TreeRole.EDITOR))],
)
async def safe_merge(
    tree_id: uuid.UUID,
    payload: SafeMergeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MergeResult:
    """Прогнать ``apply_diff_to_session`` и вернуть ``MergeResult``.

    Возвращает 200 в обоих случаях (success и aborted): aborted — это не
    server error, а валидный business-state, и UI должен уметь его
    отрендерить (показать missing-anchor конфликты).
    """
    return await apply_diff_to_session(
        session,
        tree_id=tree_id,
        diff=payload.diff_report,
        policy=payload.policy,
    )
