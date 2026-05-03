"""Match-list ingest endpoints (Phase 16.3 / ADR-0072).

Routes:

* ``POST /dna/match-list/import``  — multipart upload, парсит CSV
  через :func:`dna_analysis.match_list.parse_match_list` и persistит
  в ``dna_matches``. Идемпотентность по ``(kit_id, external_match_id)``:
  повторный импорт того же CSV → upsert, дубликатов нет.
* ``GET  /dna/matches``            — list + filters (kit_id, platform,
  min_cm, max_cm).
* ``DELETE /dna/matches``          — bulk-delete (kit_id обязателен,
  platform опционален) для re-import-flow.

Anti-drift (ADR-0072):
* No scraping. CSV upload only.
* No cross-platform identity resolution; ``resolved_person_id`` /
  ``resolution_confidence`` оставляем NULL — это работа Phase 16.5.
* ``raw_payload`` (full CSV row) сохраняется в JSONB-колонке.
* Платформенные prediction-strings нормализуются в
  ``predicted_relationship_normalized`` без переоценки.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Final

from dna_analysis.match_list import MatchListEntry, parse_match_list
from dna_analysis.match_list.dispatcher import UnsupportedPlatformError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict
from shared_models.enums import DnaPlatform
from shared_models.orm import DnaKit, DnaMatch
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.database import get_session

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class MatchListImportResponse(BaseModel):
    """Результат импорта match-list CSV (Phase 16.3)."""

    model_config = ConfigDict(extra="forbid")

    kit_id: uuid.UUID
    platform: DnaPlatform
    imported: int
    updated: int
    skipped: int
    errors: list[str]


class MatchListBulkDeleteResponse(BaseModel):
    """Результат bulk delete (для re-import flow)."""

    model_config = ConfigDict(extra="forbid")

    kit_id: uuid.UUID
    platform: DnaPlatform | None
    deleted: int


class MatchListItem(BaseModel):
    """Сжатая карточка match'а в response GET /dna/matches."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    kit_id: uuid.UUID
    platform: str | None
    external_match_id: str | None
    display_name: str | None
    match_username: str | None
    total_cm: float | None
    largest_segment_cm: float | None
    segment_count: int | None
    predicted_relationship: str | None
    predicted_relationship_normalized: str | None
    matched_person_id: uuid.UUID | None


class MatchListResponse(BaseModel):
    """list-view ответа GET /dna/matches."""

    model_config = ConfigDict(extra="forbid")

    items: list[MatchListItem]
    total: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/dna/match-list/import",
    response_model=MatchListImportResponse,
    status_code=status.HTTP_200_OK,
    tags=["match-list"],
)
async def import_match_list(
    kit_id: Annotated[uuid.UUID, Form()],
    platform: Annotated[DnaPlatform, Form()],
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MatchListImportResponse:
    """Загрузить match-list CSV в ``dna_matches``.

    Идемпотентность: upsert по ``(kit_id, external_match_id)``. Новые
    строки получают новый id; существующие — обновляются (raw_payload,
    cM-stats, normalized relationship).
    """
    kit = await session.get(DnaKit, kit_id)
    if kit is None or kit.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna kit not found",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty file payload",
        )

    try:
        entries = parse_match_list(payload, platform)
    except UnsupportedPlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unable to decode CSV: {exc.reason}",
        ) from exc

    # Загрузить все existing match'и одного kit'а одним запросом —
    # upsert делается in-memory, чтобы каждая row не уходила в roundtrip.
    existing_rows = (
        (
            await session.execute(
                select(DnaMatch).where(
                    DnaMatch.kit_id == kit_id,
                    DnaMatch.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    by_external_id: dict[str, DnaMatch] = {
        row.external_match_id: row for row in existing_rows if row.external_match_id is not None
    }

    imported = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    for entry in entries:
        try:
            existing = by_external_id.get(entry.external_match_id)
            if existing is not None:
                _apply_entry(existing, entry, kit)
                updated += 1
            else:
                session.add(_new_match(entry, kit))
                imported += 1
        except (ValueError, TypeError) as exc:
            skipped += 1
            errors.append(f"{entry.external_match_id}: {exc}")

    await session.flush()
    _LOG.info(
        "match-list import: kit=%s platform=%s imported=%d updated=%d skipped=%d",
        kit_id,
        platform.value,
        imported,
        updated,
        skipped,
    )
    return MatchListImportResponse(
        kit_id=kit_id,
        platform=platform,
        imported=imported,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )


@router.get(
    "/dna/matches",
    response_model=MatchListResponse,
    tags=["match-list"],
)
async def list_matches(
    session: Annotated[AsyncSession, Depends(get_session)],
    kit_id: Annotated[uuid.UUID | None, Query()] = None,
    platform: Annotated[DnaPlatform | None, Query()] = None,
    min_cm: Annotated[float | None, Query(ge=0)] = None,
    max_cm: Annotated[float | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchListResponse:
    """list view с фильтрами по kit/platform/cM-диапазону (Phase 16.3)."""
    filters: list[Any] = [DnaMatch.deleted_at.is_(None)]
    if kit_id is not None:
        filters.append(DnaMatch.kit_id == kit_id)
    if platform is not None:
        filters.append(DnaMatch.platform == platform.value)
    if min_cm is not None:
        filters.append(DnaMatch.total_cm >= min_cm)
    if max_cm is not None:
        filters.append(DnaMatch.total_cm <= max_cm)

    rows = (
        (
            await session.execute(
                select(DnaMatch)
                .where(*filters)
                .order_by(DnaMatch.total_cm.desc().nulls_last())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return MatchListResponse(
        items=[MatchListItem.model_validate(row) for row in rows],
        total=len(rows),
    )


@router.delete(
    "/dna/matches",
    response_model=MatchListBulkDeleteResponse,
    tags=["match-list"],
)
async def bulk_delete_matches(
    session: Annotated[AsyncSession, Depends(get_session)],
    kit_id: Annotated[uuid.UUID, Query()],
    platform: Annotated[DnaPlatform | None, Query()] = None,
) -> MatchListBulkDeleteResponse:
    """Bulk-удалить matches (для re-import flow).

    ``kit_id`` обязателен — bulk-delete без него — слишком большой
    blast radius. ``platform`` опционален: если задан, удаляем только
    matches этой платформы (полезно когда один kit содержит данные с
    нескольких платформ через разные импорты).
    """
    kit = await session.get(DnaKit, kit_id)
    if kit is None or kit.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="dna kit not found",
        )

    filters: list[Any] = [DnaMatch.kit_id == kit_id]
    if platform is not None:
        filters.append(DnaMatch.platform == platform.value)

    result = await session.execute(sa_delete(DnaMatch).where(*filters))
    deleted = int(getattr(result, "rowcount", 0) or 0)
    await session.flush()
    return MatchListBulkDeleteResponse(
        kit_id=kit_id,
        platform=platform,
        deleted=deleted,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_match(entry: MatchListEntry, kit: DnaKit) -> DnaMatch:
    """Создать ORM-инстанс ``DnaMatch`` из parsed entry."""
    return DnaMatch(
        kit_id=kit.id,
        tree_id=kit.tree_id,
        platform=entry.platform.value,
        external_match_id=entry.external_match_id,
        display_name=entry.display_name,
        match_username=entry.match_username,
        total_cm=entry.total_cm,
        largest_segment_cm=entry.longest_segment_cm,
        segment_count=entry.shared_segments_count,
        predicted_relationship=entry.predicted_relationship_raw,
        predicted_relationship_normalized=entry.predicted_relationship.value,
        shared_match_count=entry.shared_match_count,
        notes=entry.notes,
        raw_payload=entry.raw_payload,
    )


def _apply_entry(target: DnaMatch, entry: MatchListEntry, kit: DnaKit) -> None:
    """Apply parsed entry поверх existing ORM row (idempotent re-import).

    Не трогаем ``matched_person_id`` / ``resolution_confidence`` —
    это user-judgement / 16.5-resolver state, его перезатирать
    свежим CSV-импортом нельзя.
    """
    target.platform = entry.platform.value
    target.tree_id = kit.tree_id
    target.display_name = entry.display_name
    target.match_username = entry.match_username
    target.total_cm = entry.total_cm
    target.largest_segment_cm = entry.longest_segment_cm
    target.segment_count = entry.shared_segments_count
    target.predicted_relationship = entry.predicted_relationship_raw
    target.predicted_relationship_normalized = entry.predicted_relationship.value
    target.shared_match_count = entry.shared_match_count
    target.notes = entry.notes
    target.raw_payload = entry.raw_payload
