"""Phase 6.3 — match listing / detail / link endpoints (ADR-0033).

Endpoints:

    GET    /dna-kits/{kit_id}/matches?limit=50&min_cm=20[&predicted=...]
    GET    /dna-matches/{match_id}
    PATCH  /dna-matches/{match_id}/link    body: {tree_id, person_id}
    DELETE /dna-matches/{match_id}/link

Privacy guards (ADR-0012 / ADR-0014 / ADR-0033):
    - Возвращаются только агрегаты: total_cm, longest_cM, segment_count.
    - Сегменты в detail-эндпоинте — chromosome / start_bp / end_bp / cm /
      num_snps (для chromosome painter), без rsid и genotypes.
    - Cross-tree линк запрещён (409): person.tree_id != match.tree_id.
    - Список matches kit'а не требует auth в Phase 6.3 (auth — Phase 6.x);
      но любой запрос на kit, у которого ``deleted_at`` выставлен (owner
      revoke'нул consent), вернёт 404 — privacy-safe behaviour.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models.orm import DnaKit, DnaMatch, Person
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.database import get_session
from dna_service.schemas import (
    DnaMatchDetailResponse,
    DnaMatchLinkRequest,
    DnaMatchListItem,
    DnaMatchListResponse,
    DnaMatchSegmentItem,
    DnaSharedAncestorHint,
)

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


def _to_list_item(match: DnaMatch) -> DnaMatchListItem:
    """Mapper ORM → list-item Pydantic. Один источник истины."""
    return DnaMatchListItem(
        id=match.id,
        kit_id=match.kit_id,
        tree_id=match.tree_id,
        external_match_id=match.external_match_id,
        display_name=match.display_name,
        total_cm=match.total_cm,
        largest_segment_cm=match.largest_segment_cm,
        segment_count=match.segment_count,
        predicted_relationship=match.predicted_relationship,
        confidence=match.confidence,
        shared_match_count=match.shared_match_count,
        matched_person_id=match.matched_person_id,
    )


def _segments_from_provenance(provenance: dict[str, Any]) -> list[DnaMatchSegmentItem]:
    """Извлекает chromosome painting сегменты из ``provenance['segments']``.

    Хранение в provenance jsonb (ADR-0033) вместо отдельной таблицы:
    Phase 6.3 не делает migration — segments импортируются опционально
    из platform-CSV и записываются как jsonb-array. Несовместимые/пустые
    структуры → пустой список (не падаем на legacy провенансе).
    """
    raw = provenance.get("segments")
    if not isinstance(raw, list):
        return []
    items: list[DnaMatchSegmentItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            chromosome = int(entry["chromosome"])
            start_bp = int(entry["start_bp"])
            end_bp = int(entry["end_bp"])
            cm = float(entry["cm"])
        except (KeyError, TypeError, ValueError):
            # Pre-Phase 6.3 legacy provenance мог хранить «другую» структуру;
            # тихо пропускаем — это не factum-уровневая ошибка.
            continue
        num_snps_raw = entry.get("num_snps")
        try:
            num_snps = int(num_snps_raw) if num_snps_raw is not None else None
        except (TypeError, ValueError):
            num_snps = None
        items.append(
            DnaMatchSegmentItem(
                chromosome=chromosome,
                start_bp=start_bp,
                end_bp=end_bp,
                cm=cm,
                num_snps=num_snps,
            )
        )
    return items


def _shared_ancestor_hint(provenance: dict[str, Any]) -> DnaSharedAncestorHint | None:
    """Опциональная подсказка про общего предка из ``provenance['shared_ancestor_hint']``."""
    raw = provenance.get("shared_ancestor_hint")
    if not isinstance(raw, dict):
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        return None
    person_id_raw = raw.get("person_id")
    person_id: uuid.UUID | None = None
    if isinstance(person_id_raw, str):
        try:
            person_id = uuid.UUID(person_id_raw)
        except ValueError:
            person_id = None
    source = raw.get("source")
    return DnaSharedAncestorHint(
        label=label,
        person_id=person_id,
        source=source if isinstance(source, str) else None,
    )


async def _load_visible_kit(session: AsyncSession, kit_id: uuid.UUID) -> DnaKit:
    """Возвращает kit или 404, если kit отсутствует / soft-deleted.

    Privacy: если owner revoke'нул consent (kit.deleted_at установлен),
    возвращаем 404 как если бы kit'а никогда не было — никакой утечки
    «когда-то существовал».
    """
    kit = await session.get(DnaKit, kit_id)
    if kit is None or kit.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna kit not found",
        )
    return kit


@router.get(
    "/dna-kits/{kit_id}/matches",
    response_model=DnaMatchListResponse,
    tags=["matches"],
)
async def list_kit_matches(
    kit_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    min_cm: Annotated[float | None, Query(ge=0)] = None,
    predicted: Annotated[str | None, Query(max_length=128)] = None,
) -> DnaMatchListResponse:
    """Постраничный список matches kit'а, сортировка по убыванию total_cm.

    Filters:
        ``min_cm`` — минимальный total_cm (NULL-значения никогда не
        проходят cM-фильтр, как ожидаемо). По умолчанию — без фильтра.

        ``predicted`` — exact-substring по ``predicted_relationship``
        (Ancestry часто пишет «3rd cousin», MyHeritage — «3rd cousin
        once removed»). Регистр не учитывается.
    """
    await _load_visible_kit(session, kit_id)

    base_filters = [DnaMatch.kit_id == kit_id, DnaMatch.deleted_at.is_(None)]
    if min_cm is not None:
        base_filters.append(DnaMatch.total_cm >= min_cm)
    if predicted is not None and predicted.strip():
        base_filters.append(
            func.lower(DnaMatch.predicted_relationship).contains(predicted.strip().lower())
        )

    total_stmt = select(func.count()).select_from(DnaMatch).where(*base_filters)
    total = (await session.execute(total_stmt)).scalar_one()

    rows = (
        (
            await session.execute(
                select(DnaMatch)
                .where(*base_filters)
                # NULL total_cm → в самый конец, иначе DESC NULLS FIRST.
                .order_by(DnaMatch.total_cm.desc().nulls_last(), DnaMatch.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return DnaMatchListResponse(
        kit_id=kit_id,
        total=int(total),
        limit=limit,
        offset=offset,
        min_cm=min_cm,
        items=[_to_list_item(m) for m in rows],
    )


@router.get(
    "/dna-matches/{match_id}",
    response_model=DnaMatchDetailResponse,
    tags=["matches"],
)
async def get_match_detail(
    match_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DnaMatchDetailResponse:
    """Детальная карточка матча — list-поля + chromosome painting + hint."""
    match = await session.get(DnaMatch, match_id)
    if match is None or match.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna match not found",
        )
    # Если parent kit revoke'нут — match тоже исчезает для UI.
    await _load_visible_kit(session, match.kit_id)

    list_item = _to_list_item(match)
    return DnaMatchDetailResponse(
        **list_item.model_dump(),
        notes=match.notes,
        segments=_segments_from_provenance(match.provenance),
        shared_ancestor_hint=_shared_ancestor_hint(match.provenance),
    )


@router.patch(
    "/dna-matches/{match_id}/link",
    response_model=DnaMatchDetailResponse,
    tags=["matches"],
)
async def link_match_to_person(
    match_id: uuid.UUID,
    payload: DnaMatchLinkRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DnaMatchDetailResponse:
    """Привязать match к персоне в дереве.

    Гvardrails:
        - 404 если match не найден или soft-deleted.
        - 404 если person не найдена / soft-deleted.
        - 409 если ``person.tree_id != match.tree_id`` (ADR-0012/0033
          cross-tree запрет утечки DNA evidence).
        - 409 если ``payload.tree_id != match.tree_id`` — чтобы фронт
          не мог случайно линкануть match не из активного дерева.
    """
    match = await session.get(DnaMatch, match_id)
    if match is None or match.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna match not found",
        )
    if payload.tree_id != match.tree_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="payload tree_id does not match the kit's tree",
        )

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
    if person.tree_id != match.tree_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="person belongs to a different tree than the match",
        )

    match.matched_person_id = person.id
    await session.flush()
    _LOG.debug(
        "dna match linked to person: match_id=%s person_id=%s tree_id=%s",
        match.id,
        person.id,
        match.tree_id,
    )

    return DnaMatchDetailResponse(
        **_to_list_item(match).model_dump(),
        notes=match.notes,
        segments=_segments_from_provenance(match.provenance),
        shared_ancestor_hint=_shared_ancestor_hint(match.provenance),
    )


@router.delete(
    "/dna-matches/{match_id}/link",
    response_model=DnaMatchDetailResponse,
    tags=["matches"],
)
async def unlink_match(
    match_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DnaMatchDetailResponse:
    """Отвязать match от персоны.

    Идемпотентно: повторный unlink на match'е без линка возвращает
    тот же detail-payload, не пишет в БД.
    """
    match = await session.get(DnaMatch, match_id)
    if match is None or match.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna match not found",
        )
    if match.matched_person_id is not None:
        match.matched_person_id = None
        await session.flush()
        _LOG.debug("dna match unlinked: match_id=%s", match.id)

    return DnaMatchDetailResponse(
        **_to_list_item(match).model_dump(),
        notes=match.notes,
        segments=_segments_from_provenance(match.provenance),
        shared_ancestor_hint=_shared_ancestor_hint(match.provenance),
    )
