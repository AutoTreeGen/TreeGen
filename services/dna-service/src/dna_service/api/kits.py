"""DnaKit endpoints (Phase 7.3 / ADR-0023).

Сейчас один endpoint — `PATCH /dna-kits/{kit_id}/link-person` —
линкует ДНК-кит к персоне в дереве (или развязывает при `person_id=null`).
Этот линк позволяет inference-engine от Phase 7.3.1 ходить
«kit → person» когда собирает DNA-aggregate для context'а.

Phase 7.3 cross-tree guard: если ``person.tree_id != kit.tree_id``,
возвращаем 409 — линк между деревьями запрещён, чтобы не утекало DNA
evidence через границы дерева (privacy ADR-0012).
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models.orm import DnaKit, Person
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.database import get_session
from dna_service.schemas import KitLinkPersonRequest, KitResponse

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


def _to_response(kit: DnaKit) -> KitResponse:
    return KitResponse(
        id=kit.id,
        tree_id=kit.tree_id,
        owner_user_id=kit.owner_user_id,
        person_id=kit.person_id,
        source_platform=kit.source_platform,
        external_kit_id=kit.external_kit_id,
        display_name=kit.display_name,
        ethnicity_population=kit.ethnicity_population,
    )


@router.patch(
    "/dna-kits/{kit_id}/link-person",
    response_model=KitResponse,
    tags=["kits"],
)
async def link_kit_to_person(
    kit_id: uuid.UUID,
    payload: KitLinkPersonRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KitResponse:
    """Привязать (или отвязать) DnaKit к персоне в его дереве.

    - ``person_id=null`` → очистить связь (unlink). 200.
    - ``person_id=<uuid>`` → проверить существование персоны и совпадение
      ``tree_id`` с китом, затем установить ``DnaKit.person_id``. 200.

    Errors:
        - 404 — kit не найден.
        - 404 — person не найдена / удалена.
        - 409 — person.tree_id != kit.tree_id (cross-tree линк запрещён).
    """
    kit = await session.get(DnaKit, kit_id)
    if kit is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna kit not found",
        )

    if payload.person_id is None:
        if kit.person_id is None:
            # Идемпотентно — повторный unlink без изменений.
            return _to_response(kit)
        kit.person_id = None
        await session.flush()
        _LOG.debug("dna kit unlinked from person: kit_id=%s", kit.id)
        return _to_response(kit)

    person = (
        await session.execute(
            select(Person).where(
                Person.id == payload.person_id,
                Person.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if person is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="person not found",
        )
    if person.tree_id != kit.tree_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="person belongs to a different tree than the kit",
        )

    kit.person_id = person.id
    await session.flush()
    _LOG.debug(
        "dna kit linked to person: kit_id=%s person_id=%s tree_id=%s",
        kit.id,
        person.id,
        kit.tree_id,
    )
    return _to_response(kit)
