"""FastAPI router для archive registry (Phase 22.1).

Endpoints:

* ``GET    /archives/registry?country=&record_type=&year_from=&year_to=`` —
  поиск + ranking, возвращает list[ArchiveListingRead] с ``rank_score``.
* ``GET    /archives/registry/{listing_id}`` — детали одного listing'а.
* ``POST   /archives/registry`` (admin) — создать.
* ``PATCH  /archives/registry/{listing_id}`` (admin) — обновить.
* ``DELETE /archives/registry/{listing_id}`` (admin) — удалить.

Admin-guard вынесен в :func:`require_admin` — проверяет совпадение
``ClerkClaims.email`` с ``settings.admin_email``. 403 без подсказки,
какой email был ожидаем.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from shared_models.auth import ClerkClaims
from shared_models.orm.archive_listing import RecordType
from sqlalchemy.ext.asyncio import AsyncSession

from archive_service.auth import get_current_claims
from archive_service.config import Settings, get_settings
from archive_service.database import get_session
from archive_service.registry import repo
from archive_service.registry.schemas import (
    ArchiveListingCreate,
    ArchiveListingRead,
    ArchiveListingUpdate,
    ArchiveRegistryResponse,
)
from archive_service.registry.scorer import compute_privacy_blocked, score_listing

router = APIRouter(prefix="/archives/registry", tags=["registry"])


def require_admin(
    claims: Annotated[ClerkClaims, Depends(get_current_claims)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClerkClaims:
    """403 если caller — не admin.

    Сравниваем по lower-case + strip, mirror parser-service convention.
    Если у claims нет email (Clerk frontend-tokens по дефолту его не
    кладут) — тоже 403, потому что мы не можем доказать identity.
    """
    expected = settings.admin_email.strip().lower()
    actual = (claims.email or "").strip().lower()
    if not actual or actual != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin-only endpoint",
        )
    return claims


@router.get(
    "",
    response_model=ArchiveRegistryResponse,
    summary="Search archive registry by country + record type + year window.",
)
async def search_registry(
    session: Annotated[AsyncSession, Depends(get_session)],
    country: Annotated[
        str | None,
        Query(
            min_length=2,
            max_length=2,
            pattern=r"^[A-Z]{2}$",
            description="ISO 3166-1 alpha-2 (e.g. UA, RU, PL).",
        ),
    ] = None,
    record_type: Annotated[
        RecordType | None,
        Query(description="Filter архивы, которые держат этот тип записей."),
    ] = None,
    year_from: Annotated[
        int | None,
        Query(ge=1100, le=2100, description="Начало искомого периода (inclusive)."),
    ] = None,
    year_to: Annotated[
        int | None,
        Query(ge=1100, le=2100, description="Конец искомого периода (inclusive)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ArchiveRegistryResponse:
    """Вернуть архивы, отсортированные по rank_score (best first).

    Filter в DB: ``country`` + ``record_type``. Year — только в ranking
    (overlap с listing's [year_from, year_to]).
    """
    if year_from is not None and year_to is not None and year_to < year_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="year_to must be >= year_from",
        )

    rt_value = record_type.value if record_type else None
    listings = await repo.list_archives(
        session,
        country=country,
        record_type=rt_value,
    )

    scored: list[tuple[float, bool, dict[str, object]]] = []
    for listing in listings:
        as_dict = listing.to_dict()
        rank = score_listing(
            as_dict,
            record_type=rt_value,
            year_from=year_from,
            year_to=year_to,
        )
        privacy_blocked = compute_privacy_blocked(
            as_dict,
            year_from=year_from,
            year_to=year_to,
        )
        scored.append((rank, privacy_blocked, as_dict))

    # Стабильная сортировка: rank desc, затем (country, name) asc для
    # детерминированного порядка при одинаковых rank.
    scored.sort(key=lambda t: (-t[0], t[2]["country"], t[2]["name"]))

    items: list[ArchiveListingRead] = []
    for rank, blocked, data in scored[:limit]:
        items.append(
            ArchiveListingRead(
                **data,
                rank_score=round(rank, 4),
                privacy_blocked=blocked,
            )
        )

    return ArchiveRegistryResponse(
        items=items,
        total=len(scored),
        country=country,
        record_type=record_type,
        year_from=year_from,
        year_to=year_to,
    )


@router.get(
    "/{listing_id}",
    response_model=ArchiveListingRead,
    summary="Get a single archive listing by id.",
)
async def get_listing(
    listing_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArchiveListingRead:
    """Вернуть один listing. 404 если не существует."""
    listing = await repo.get_archive(session, listing_id)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="archive listing not found",
        )
    return ArchiveListingRead(**listing.to_dict())


@router.post(
    "",
    response_model=ArchiveListingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new archive listing (admin only).",
)
async def create_listing(
    payload: ArchiveListingCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[ClerkClaims, Depends(require_admin)],
) -> ArchiveListingRead:
    """Создать listing. Body — ArchiveListingCreate.

    ``record_types`` валидируется как list[RecordType] на Pydantic-уровне;
    в DB кладётся как list[str] (значения enum).
    """
    data = payload.model_dump()
    data["record_types"] = [rt.value if hasattr(rt, "value") else rt for rt in data["record_types"]]
    data["access_mode"] = (
        data["access_mode"].value if hasattr(data["access_mode"], "value") else data["access_mode"]
    )
    listing = await repo.create_archive(session, data)
    return ArchiveListingRead(**listing.to_dict())


@router.patch(
    "/{listing_id}",
    response_model=ArchiveListingRead,
    summary="Update an existing archive listing (admin only).",
)
async def update_listing(
    listing_id: uuid.UUID,
    payload: ArchiveListingUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[ClerkClaims, Depends(require_admin)],
) -> ArchiveListingRead:
    """Patch listing. Body — частичный ``ArchiveListingUpdate``.

    Только переданные поля (``exclude_unset=True``) попадают в UPDATE.
    """
    data = payload.model_dump(exclude_unset=True)
    if "record_types" in data and data["record_types"] is not None:
        data["record_types"] = [
            rt.value if hasattr(rt, "value") else rt for rt in data["record_types"]
        ]
    if "access_mode" in data and data["access_mode"] is not None:
        data["access_mode"] = (
            data["access_mode"].value
            if hasattr(data["access_mode"], "value")
            else data["access_mode"]
        )
    listing = await repo.update_archive(session, listing_id, data)
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="archive listing not found",
        )
    return ArchiveListingRead(**listing.to_dict())


@router.delete(
    "/{listing_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an archive listing (admin only).",
    response_class=Response,
)
async def delete_listing(
    listing_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    _admin: Annotated[ClerkClaims, Depends(require_admin)],
) -> Response:
    """Hard-delete listing. 404 если не существует, 204 при успехе."""
    deleted = await repo.delete_archive(session, listing_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="archive listing not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["require_admin", "router"]
