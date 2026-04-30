"""Wikimedia Commons → MultimediaObject importer (Phase 9.1).

См. ADR-0058 — обоснование reuse'а ``multimedia_objects`` table вместо
заведения отдельной ``place_images`` таблицы.

Контракт importer'а:

* Получает ``tree_id`` + ``place_id``, читает Place (lat/lon /
  canonical_name) и зовёт :class:`WikimediaCommonsClient`. Если у
  Place'а есть координаты — geosearch; иначе — full-text.
* Persists каждое уникальное изображение как ``MultimediaObject``
  с полным provenance + license-trail и связывает с Place'ом через
  ``EntityMultimedia(entity_type='place')``.
* Идемпотентен по ``provenance->>'commons_page_url'`` в пределах одного
  ``tree_id``: повторный fetch не дублирует записи. Это ключ дедупа,
  потому что Commons URL'ы стабильны при rename'ах через redirect, а
  raw image URL — нет.

Returns: :class:`WikimediaImportStats` со счётчиками — сколько найдено,
сколько вставлено, сколько пропущено как уже существующих.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shared_models import set_audit_skip
from shared_models.enums import EntityStatus
from shared_models.orm import EntityMultimedia, MultimediaObject, Place
from shared_models.types import new_uuid
from sqlalchemy import select
from wikimedia_commons_client import (
    CommonsImage,
    WikimediaCommonsClient,
    WikimediaCommonsConfig,
    WikimediaCommonsError,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Wikimedia source-tag в provenance — используем как enum-style константу
# (extending ImportSourceKind enum для одного fetch'а — overkill, у нас
# нет ImportJob row для Wikimedia в этой фазе, см. ADR-0058 §«No ImportJob»).
WIKIMEDIA_SOURCE = "wikimedia_commons"

# Дефолтный радиус для geosearch — 5 км вокруг lat/lon. Большинство
# place-events происходят в пределах одного населённого пункта.
DEFAULT_GEOSEARCH_RADIUS_M = 5000

# Дефолтный лимит изображений на один Place. Не хочется заваливать UI
# десятками котиков из соседнего парка; первые 10 релевантных хватит.
DEFAULT_LIMIT = 10


@dataclass(frozen=True, kw_only=True, slots=True)
class WikimediaImportStats:
    """Сводка одного fetch'а.

    Attributes:
        place_id: ID Place'а, для которого делали fetch.
        search_strategy: ``"geosearch"`` или ``"text"``.
        fetched: сколько изображений вернул Commons.
        created: сколько новых ``MultimediaObject`` rows вставлено.
        skipped_existing: сколько пропущено как уже существующих
            (provenance.commons_page_url уже встречался в этом дереве).
    """

    place_id: uuid.UUID
    search_strategy: str
    fetched: int
    created: int
    skipped_existing: int


class WikimediaImporterError(Exception):
    """Importer-уровень ошибки (Place not found, и т.п.)."""


class PlaceNotFoundError(WikimediaImporterError):
    """Place не существует в указанном дереве, либо soft-deleted."""


async def import_wikimedia_for_place(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    place_id: uuid.UUID,
    user_agent: str,
    limit: int = DEFAULT_LIMIT,
    radius_m: int = DEFAULT_GEOSEARCH_RADIUS_M,
    commons_client: WikimediaCommonsClient | None = None,
) -> WikimediaImportStats:
    """Подтягивает изображения Place'а из Wikimedia Commons.

    Args:
        session: async-сессия (commit/rollback — на caller).
        tree_id: дерево, к которому принадлежит Place.
        place_id: целевой Place. Должен быть live (``deleted_at IS NULL``)
            и принадлежать ``tree_id``.
        user_agent: WMF UA-policy строка (caller получает из settings).
        limit: max изображений к fetch (1..500).
        radius_m: geosearch радиус, метры (10..10000). Игнорируется при
            text-fallback'е (когда у Place нет координат).
        commons_client: optional injection для тестов; если ``None``,
            создаём собственный с ``user_agent``.

    Returns:
        :class:`WikimediaImportStats` со счётчиками.

    Raises:
        PlaceNotFoundError: Place не существует или not in this tree.
        WikimediaCommonsError: HTTP/parsing ошибка от Commons.
    """
    place = await session.scalar(
        select(Place).where(
            Place.id == place_id,
            Place.tree_id == tree_id,
            Place.deleted_at.is_(None),
        )
    )
    if place is None:
        msg = f"Place {place_id} not found in tree {tree_id}"
        raise PlaceNotFoundError(msg)

    images, strategy = await _search_commons(
        place=place,
        user_agent=user_agent,
        limit=limit,
        radius_m=radius_m,
        commons_client=commons_client,
    )

    if not images:
        return WikimediaImportStats(
            place_id=place.id,
            search_strategy=strategy,
            fetched=0,
            created=0,
            skipped_existing=0,
        )

    existing_urls = await _existing_commons_urls(
        session, tree_id=tree_id, page_urls=[str(img.page_url) for img in images]
    )

    now = dt.datetime.now(dt.UTC)
    created = 0
    skipped = 0

    set_audit_skip(session.sync_session, True)
    try:
        for img in images:
            page_url = str(img.page_url)
            if page_url in existing_urls:
                skipped += 1
                continue

            multimedia_id = new_uuid()
            session.add(
                MultimediaObject(
                    id=multimedia_id,
                    tree_id=tree_id,
                    object_type="image",
                    storage_url=str(img.image_url),
                    mime_type=img.mime,
                    sha256=None,  # bytes хранятся на Commons CDN, локально не качаем
                    caption=img.title,
                    object_metadata=_build_object_metadata(img),
                    status=EntityStatus.PROBABLE.value,
                    confidence_score=0.7,
                    provenance=_build_provenance(img, fetched_at=now),
                    version_id=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            # Flush до EntityMultimedia: explicit-PK pattern + FK dep сбивают
            # дефолтный SQLAlchemy unit-of-work order, и без flush'а row
            # entity_multimedia может улететь до multimedia_objects, что даст
            # FK violation. Один лишний flush на изображение — допустимо
            # при ожидаемом N≤10 на запрос.
            await session.flush()
            session.add(
                EntityMultimedia(
                    id=new_uuid(),
                    multimedia_id=multimedia_id,
                    entity_type="place",
                    entity_id=place.id,
                    role="primary" if created == 0 else "supplemental",
                    created_at=now,
                    updated_at=now,
                )
            )
            existing_urls.add(page_url)  # защита от дубля внутри одного batch'а
            created += 1
    finally:
        set_audit_skip(session.sync_session, False)

    if created:
        await session.flush()

    logger.info(
        "wikimedia_import: tree=%s place=%s strategy=%s fetched=%d created=%d skipped=%d",
        tree_id,
        place.id,
        strategy,
        len(images),
        created,
        skipped,
    )

    return WikimediaImportStats(
        place_id=place.id,
        search_strategy=strategy,
        fetched=len(images),
        created=created,
        skipped_existing=skipped,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _search_commons(
    *,
    place: Place,
    user_agent: str,
    limit: int,
    radius_m: int,
    commons_client: WikimediaCommonsClient | None,
) -> tuple[list[CommonsImage], str]:
    """Делает один поиск в Commons. Возвращает (images, strategy).

    Стратегия:
      1) geosearch если у Place'а есть lat+lon.
      2) full-text по canonical_name иначе.
    """
    has_coords = place.latitude is not None and place.longitude is not None

    if commons_client is not None:
        if has_coords:
            assert place.latitude is not None
            assert place.longitude is not None
            images = await commons_client.search_by_coordinates(
                latitude=place.latitude,
                longitude=place.longitude,
                radius_m=radius_m,
                limit=limit,
            )
            return images, "geosearch"
        images = await commons_client.search_by_title(query=place.canonical_name, limit=limit)
        return images, "text"

    config = WikimediaCommonsConfig(user_agent=user_agent)
    async with WikimediaCommonsClient(config=config) as client:
        try:
            if has_coords:
                assert place.latitude is not None
                assert place.longitude is not None
                images = await client.search_by_coordinates(
                    latitude=place.latitude,
                    longitude=place.longitude,
                    radius_m=radius_m,
                    limit=limit,
                )
                return images, "geosearch"
            images = await client.search_by_title(query=place.canonical_name, limit=limit)
            return images, "text"
        except WikimediaCommonsError:
            # Re-raise как есть — caller (HTTP-handler) маппит в HTTP status.
            raise


async def _existing_commons_urls(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    page_urls: list[str],
) -> set[str]:
    """SELECT существующих MultimediaObject rows по provenance->>'commons_page_url'.

    Используется для дедупа: возвращает множество уже импортированных
    Commons URL'ов в этом дереве. Игнорирует soft-deleted records — они
    «зарезервированы», и их повторный импорт нежелателен (юзер их явно
    скрыл).
    """
    if not page_urls:
        return set()
    stmt = select(MultimediaObject.provenance["commons_page_url"].astext.label("url")).where(
        MultimediaObject.tree_id == tree_id,
        MultimediaObject.provenance["source"].astext == WIKIMEDIA_SOURCE,
        MultimediaObject.provenance["commons_page_url"].astext.in_(page_urls),
    )
    rows = (await session.execute(stmt)).all()
    return {row.url for row in rows if row.url is not None}


def _build_provenance(img: CommonsImage, *, fetched_at: dt.datetime) -> dict[str, object]:
    """Полное provenance для MultimediaObject.

    Содержит:
      * source = wikimedia_commons (используется как ключ дедупа +
        для UI-фильтров «show only Commons sources»)
      * commons_page_url = unique key для дедупа
      * license_short_name + attribution_required = legal trail (даже
        если object_metadata случайно потеряется, license-обязательства
        восстановимы из provenance)
      * fetched_at — для retention/refresh policies
    """
    license_short_name = img.license.short_name if img.license is not None else None
    return {
        "source": WIKIMEDIA_SOURCE,
        "commons_page_url": str(img.page_url),
        "fetched_at": fetched_at.isoformat(),
        "license_short_name": license_short_name,
        "attribution_required": img.attribution.required,
    }


def _build_object_metadata(img: CommonsImage) -> dict[str, object]:
    """object_metadata — UI-relevant полный набор Commons-полей.

    В отличие от provenance, тут можно обновлять при refresh'е (provenance
    остаётся стабильным). Эти поля рендерятся UI-панелью изображений.
    """
    metadata: dict[str, object] = {
        "title": img.title,
        "image_url": str(img.image_url),
    }
    if img.thumb_url is not None:
        metadata["thumb_url"] = str(img.thumb_url)
    if img.width is not None:
        metadata["width"] = img.width
    if img.height is not None:
        metadata["height"] = img.height
    if img.license is not None:
        metadata["license_short_name"] = img.license.short_name
        if img.license.url is not None:
            metadata["license_url"] = str(img.license.url)
    if img.attribution.credit_html is not None:
        metadata["credit_html"] = img.attribution.credit_html
    metadata["attribution_required"] = img.attribution.required
    return metadata
