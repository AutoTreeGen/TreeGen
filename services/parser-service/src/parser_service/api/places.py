"""Places API — Wikimedia Commons imagery endpoints (Phase 9.1).

См. ADR-0058. Эндпоинты place-scoped, под /trees/{tree_id}/places/{place_id}/.

Контракт:

* ``POST .../wikimedia-fetch`` — синхронный fetch изображений Place'а
  из Commons. Идемпотентен (повторный вызов не дублирует записи).
  Требует EDITOR (добавляет данные в дерево).
* ``GET  .../wikimedia-images`` — список уже импортированных Commons-
  изображений, с license/attribution для UI-рендеринга. Требует VIEWER.

Wikimedia не имеет OAuth — UA из ``Settings.wikimedia_user_agent``
автоматически уходит во все запросы (WMF policy).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_models import TreeRole
from shared_models.orm import EntityMultimedia, MultimediaObject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from wikimedia_commons_client import (
    ClientError,
    RateLimitError,
    ServerError,
    WikimediaCommonsError,
)
from wikimedia_commons_client import (
    NotFoundError as WikimediaNotFoundError,
)

from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.schemas import (
    WikimediaFetchResponse,
    WikimediaImageItem,
    WikimediaImageListResponse,
)
from parser_service.services.permissions import require_tree_role
from parser_service.services.wikimedia_importer import (
    DEFAULT_GEOSEARCH_RADIUS_M,
    DEFAULT_LIMIT,
    WIKIMEDIA_SOURCE,
    PlaceNotFoundError,
    import_wikimedia_for_place,
)

router = APIRouter()


@router.post(
    "/trees/{tree_id}/places/{place_id}/wikimedia-fetch",
    response_model=WikimediaFetchResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_tree_role(TreeRole.EDITOR))],
)
async def wikimedia_fetch(
    tree_id: uuid.UUID,
    place_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=50)] = DEFAULT_LIMIT,
    radius_m: Annotated[int, Query(ge=10, le=10_000)] = DEFAULT_GEOSEARCH_RADIUS_M,
) -> WikimediaFetchResponse:
    """Подтянуть изображения Place'а из Wikimedia Commons.

    Делает один HTTP-запрос к Commons (geosearch если у Place'а есть
    координаты, иначе full-text по canonical_name) и записывает новые
    изображения как ``MultimediaObject`` rows. Идемпотентен по
    ``provenance.commons_page_url``.

    Failures:

    * ``404`` — Place не найден или не принадлежит tree.
    * ``429`` — Commons rate-limited (несём header дальше).
    * ``502 Bad Gateway`` — Commons вернул 5xx после retry'ев.
    * ``503 Service Unavailable`` — Commons был недоступен / network error.
    """
    try:
        stats = await import_wikimedia_for_place(
            session,
            tree_id=tree_id,
            place_id=place_id,
            user_agent=settings.wikimedia_user_agent,
            limit=limit,
            radius_m=radius_m,
        )
        await session.commit()
    except PlaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RateLimitError as exc:
        # Прокидываем retry_after caller'у через header. Сам ответ — 429.
        headers: dict[str, str] = {}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(int(exc.retry_after))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Wikimedia Commons rate-limited; try again later.",
            headers=headers,
        ) from exc
    except ServerError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wikimedia Commons upstream error.",
        ) from exc
    except (ClientError, WikimediaNotFoundError) as exc:
        # 4xx от Commons — наш bug либо Place'а реально нет в Commons.
        # Возвращаем 502, чтобы UI не путал с собственным 404 на Place.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Wikimedia Commons returned an error: {exc}",
        ) from exc
    except WikimediaCommonsError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Wikimedia Commons request failed.",
        ) from exc

    return WikimediaFetchResponse(
        place_id=stats.place_id,
        search_strategy="geosearch" if stats.search_strategy == "geosearch" else "text",
        fetched=stats.fetched,
        created=stats.created,
        skipped_existing=stats.skipped_existing,
    )


@router.get(
    "/trees/{tree_id}/places/{place_id}/wikimedia-images",
    response_model=WikimediaImageListResponse,
    dependencies=[Depends(require_tree_role(TreeRole.VIEWER))],
)
async def list_wikimedia_images(
    tree_id: uuid.UUID,
    place_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WikimediaImageListResponse:
    """Вернуть импортированные Commons-изображения этого Place'а.

    Read-only. Только активные (deleted_at IS NULL) MultimediaObject'ы
    с provenance.source = wikimedia_commons. Список сортирован по дате
    fetch'а, наиболее ранние — первыми (relative-stable порядок).
    """
    stmt = (
        select(MultimediaObject)
        .join(
            EntityMultimedia,
            EntityMultimedia.multimedia_id == MultimediaObject.id,
        )
        .where(
            MultimediaObject.tree_id == tree_id,
            MultimediaObject.deleted_at.is_(None),
            MultimediaObject.provenance["source"].astext == WIKIMEDIA_SOURCE,
            EntityMultimedia.entity_type == "place",
            EntityMultimedia.entity_id == place_id,
        )
        .order_by(MultimediaObject.created_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()

    items = [_to_image_item(row) for row in rows]
    return WikimediaImageListResponse(place_id=place_id, items=items)


def _to_image_item(mm: MultimediaObject) -> WikimediaImageItem:
    """Adapter MultimediaObject → WikimediaImageItem для UI.

    license/attribution собираются с приоритетом из object_metadata
    (там полный набор + thumb_url), а fallback на provenance — для
    legacy rows на случай, если object_metadata случайно пустой.
    """
    metadata = mm.object_metadata or {}
    provenance = mm.provenance or {}

    fetched_at_raw = provenance.get("fetched_at")
    fetched_at: dt.datetime | None = None
    if isinstance(fetched_at_raw, str):
        try:
            fetched_at = dt.datetime.fromisoformat(fetched_at_raw)
        except ValueError:
            fetched_at = None

    return WikimediaImageItem(
        id=mm.id,
        title=str(metadata.get("title") or mm.caption or mm.storage_url),
        image_url=str(metadata.get("image_url") or mm.storage_url),
        thumb_url=_optional_str(metadata.get("thumb_url")),
        page_url=str(provenance.get("commons_page_url") or ""),
        license_short_name=_optional_str(
            metadata.get("license_short_name") or provenance.get("license_short_name")
        ),
        license_url=_optional_str(metadata.get("license_url")),
        credit_html=_optional_str(metadata.get("credit_html")),
        attribution_required=bool(
            metadata.get("attribution_required", provenance.get("attribution_required", True))
        ),
        width=_optional_int(metadata.get("width")),
        height=_optional_int(metadata.get("height")),
        fetched_at=fetched_at,
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
