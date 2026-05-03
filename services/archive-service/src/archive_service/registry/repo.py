"""DB-чтение и admin-CRUD для archive_listings (Phase 22.1).

Чистый async-SQLAlchemy 2: select/insert/update/delete на ORM-class,
без сырого SQL. Filter-логика DB-side, ranking — Python-side через
:func:`scorer.score_listing` (мелкая БД < 100 строк сортируется в памяти
дешевле, чем городить SQL-CASE expression).
"""

from __future__ import annotations

import uuid
from typing import Any

from shared_models.orm.archive_listing import ArchiveListing
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession


async def list_archives(
    session: AsyncSession,
    *,
    country: str | None = None,
    record_type: str | None = None,
) -> list[ArchiveListing]:
    """Достать все listing'и под (опциональные) country + record_type фильтры.

    Year фильтр НЕ применяется в DB: ранжирование scorer'а оценивает
    overlap, а cut-off мы оставляем UI'ю — пустой ответ полезен реже,
    чем «слабо-overlap'нутый, но единственный».

    ``record_type`` filter использует JSONB ``?`` operator
    (``record_types ? 'civil_birth'``), который перекладывается на
    GIN-индекс ``ix_archive_listings_record_types_gin``.
    """
    stmt = select(ArchiveListing)
    if country:
        stmt = stmt.where(ArchiveListing.country == country)
    if record_type:
        # JSONB ? operator: «contains key/element».
        stmt = stmt.where(text("record_types ? :rt").bindparams(rt=record_type))
    result = await session.execute(stmt.order_by(ArchiveListing.country, ArchiveListing.name))
    return list(result.scalars().all())


async def get_archive(session: AsyncSession, listing_id: uuid.UUID) -> ArchiveListing | None:
    """Достать один listing по PK."""
    stmt = select(ArchiveListing).where(ArchiveListing.id == listing_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_archive(
    session: AsyncSession,
    payload: dict[str, Any],
) -> ArchiveListing:
    """Создать listing. Caller отвечает за commit."""
    listing = ArchiveListing(**payload)
    session.add(listing)
    await session.flush()
    await session.refresh(listing)
    return listing


async def update_archive(
    session: AsyncSession,
    listing_id: uuid.UUID,
    payload: dict[str, Any],
) -> ArchiveListing | None:
    """Patch listing in place. None если не найдено."""
    listing = await get_archive(session, listing_id)
    if listing is None:
        return None
    for field, value in payload.items():
        setattr(listing, field, value)
    await session.flush()
    await session.refresh(listing)
    return listing


async def delete_archive(session: AsyncSession, listing_id: uuid.UUID) -> bool:
    """Hard delete listing. True если строка удалена."""
    stmt = delete(ArchiveListing).where(ArchiveListing.id == listing_id)
    result = await session.execute(stmt)
    # ``rowcount`` живёт на CursorResult (DML execute), но статический тип
    # ``Result[Any]`` его не объявляет — runtime-доступ через getattr.
    return bool(getattr(result, "rowcount", 0))


__all__ = [
    "create_archive",
    "delete_archive",
    "get_archive",
    "list_archives",
    "update_archive",
]
