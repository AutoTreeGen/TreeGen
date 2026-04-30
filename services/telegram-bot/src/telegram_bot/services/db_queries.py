"""Read-only DB queries для command-handler'ов (Phase 14.1, ADR-0056).

Bot читает домен напрямую через ORM (а не через HTTP к parser-service):

* монорепо share'ит ``shared-models`` ORM, отдельный auth-flow между
  сервисами не нужен;
* команды read-only — нет риска нарушить инварианты mutating-flows;
* HTTP к parser-service потребовал бы дизайн service-token auth + retry
  + circuit-breaker, что существенно расширяет scope (см. ADR-0056
  §«HTTP vs direct DB»).

Все функции принимают ``AsyncSession`` и возвращают plain dict/dataclass —
без aiogram-зависимостей, удобно для unit-тестов.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from shared_models.orm import (
    Event,
    EventParticipant,
    ImportJob,
    Name,
    Person,
    TelegramUserLink,
    Tree,
)
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ImportSummary:
    """Лёгкий снапшот ImportJob для вывода в боте."""

    id: uuid.UUID
    tree_id: uuid.UUID
    status: str
    source_filename: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None


@dataclass(frozen=True)
class TreeSummary:
    """Active tree info для ``/tree``."""

    id: uuid.UUID
    name: str
    persons_count: int
    last_updated_at: dt.datetime | None  # MAX(persons.updated_at) — proxy для «активности»


@dataclass(frozen=True)
class PersonSearchHit:
    """Один результат поиска для ``/persons <name>``."""

    id: uuid.UUID
    primary_name: str | None
    sex: str


@dataclass(frozen=True)
class InlineSearchHit:
    """Один результат для inline-search (Phase 14.2).

    Отличается от ``PersonSearchHit`` наличием ``birth_year`` /
    ``birth_place_label`` — нужно чтобы заполнить ``description``
    у ``InlineQueryResultArticle``.
    """

    id: uuid.UUID
    tree_id: uuid.UUID
    primary_name: str | None
    birth_year: int | None
    birth_place_label: str | None


async def resolve_user_id_from_chat(session: AsyncSession, *, tg_chat_id: int) -> uuid.UUID | None:
    """Resolve linked TreeGen user_id by Telegram chat_id.

    Возвращает ``None`` если chat не залинкован или связь revoked.
    """
    res = await session.execute(
        select(TelegramUserLink.user_id).where(
            TelegramUserLink.tg_chat_id == tg_chat_id,
            TelegramUserLink.revoked_at.is_(None),
        )
    )
    return res.scalar_one_or_none()


async def fetch_telegram_link(session: AsyncSession, *, tg_chat_id: int) -> TelegramUserLink | None:
    """Достать активную TelegramUserLink-row по tg_chat_id.

    Используется ``/subscribe`` для toggle'а ``notifications_enabled``.
    """
    res = await session.execute(
        select(TelegramUserLink).where(
            TelegramUserLink.tg_chat_id == tg_chat_id,
            TelegramUserLink.revoked_at.is_(None),
        )
    )
    return res.scalar_one_or_none()


async def fetch_active_tree(session: AsyncSession, *, user_id: uuid.UUID) -> TreeSummary | None:
    """Active tree = первое owned-дерево по ``created_at ASC`` (ADR-0056).

    Простейший rule. Возвращает ``None`` если у user'а нет деревьев.
    Member-trees (Phase 11.0 sharing) не учитываются — Phase 14.2 если
    понадобится.
    """
    res = await session.execute(
        select(Tree)
        .where(
            Tree.owner_user_id == user_id,
            Tree.deleted_at.is_(None),
        )
        .order_by(Tree.created_at.asc())
        .limit(1)
    )
    tree = res.scalar_one_or_none()
    if tree is None:
        return None

    persons_count = await session.scalar(
        select(func.count(Person.id)).where(
            Person.tree_id == tree.id,
            Person.deleted_at.is_(None),
        )
    )
    last_updated = await session.scalar(
        select(func.max(Person.updated_at)).where(
            Person.tree_id == tree.id,
            Person.deleted_at.is_(None),
        )
    )
    return TreeSummary(
        id=tree.id,
        name=tree.name,
        persons_count=int(persons_count or 0),
        last_updated_at=last_updated,
    )


async def fetch_recent_imports(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = 5
) -> list[ImportSummary]:
    """Последние N import jobs по owned-деревьям user'а (newest first).

    JOIN'ит ``trees`` по ``owner_user_id``: только imports тех деревьев,
    которые user owns. Member-imports (если user — editor другого
    дерева) не показываем — Phase 14.2.
    """
    res = await session.execute(
        select(ImportJob)
        .join(Tree, Tree.id == ImportJob.tree_id)
        .where(
            Tree.owner_user_id == user_id,
            Tree.deleted_at.is_(None),
        )
        .order_by(ImportJob.created_at.desc())
        .limit(limit)
    )
    jobs = res.scalars().all()
    return [
        ImportSummary(
            id=job.id,
            tree_id=job.tree_id,
            status=job.status,
            source_filename=job.source_filename,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
        for job in jobs
    ]


async def search_persons_in_active_tree(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    query: str,
    limit: int = 5,
) -> tuple[uuid.UUID | None, list[PersonSearchHit]]:
    """Substring search по given_name+surname в active tree.

    Возвращает ``(active_tree_id, hits)``. ``active_tree_id`` будет
    ``None`` если у user'а вообще нет деревьев.

    Намеренно простой ILIKE, без phonetic — bot UI не предлагает
    переключение режима. Phonetic search доступен на web (`/trees/[id]/persons`).
    """
    active = await fetch_active_tree(session, user_id=user_id)
    if active is None:
        return (None, [])

    pattern = f"%{query.strip()}%"
    res = await session.execute(
        select(Person)
        .where(
            Person.tree_id == active.id,
            Person.deleted_at.is_(None),
            Person.id.in_(
                select(Name.person_id).where(
                    Name.deleted_at.is_(None),
                    (Name.given_name.ilike(pattern)) | (Name.surname.ilike(pattern)),
                )
            ),
        )
        .order_by(Person.created_at.asc())
        .limit(limit)
    )
    persons = res.scalars().all()

    # Соберём primary_name через дополнительный SELECT (один round-trip
    # batch-load на всех найденных).
    hits: list[PersonSearchHit] = []
    person_ids = [p.id for p in persons]
    if not person_ids:
        return (active.id, hits)

    name_res = await session.execute(
        select(Name)
        .where(Name.person_id.in_(person_ids), Name.deleted_at.is_(None))
        .order_by(Name.person_id, Name.sort_order.asc())
    )
    primary_by_pid: dict[uuid.UUID, str] = {}
    for name in name_res.scalars().all():
        if name.person_id in primary_by_pid:
            continue  # уже есть первый по sort_order
        parts = [name.given_name, name.surname]
        joined = " ".join(p for p in parts if p)
        if joined:
            primary_by_pid[name.person_id] = joined

    for p in persons:
        hits.append(
            PersonSearchHit(
                id=p.id,
                primary_name=primary_by_pid.get(p.id),
                sex=p.sex,
            )
        )
    return (active.id, hits)


async def inline_search_persons_in_active_tree(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    surname: str,
    given: str | None,
    year: int | None,
    limit: int = 5,
) -> tuple[uuid.UUID | None, list[InlineSearchHit]]:
    """Inline-search для Phase 14.2 ``@bot Иванов 1850`` запросов.

    Тот же паттерн что и :func:`search_persons_in_active_tree`, но:

    * ``surname`` обязателен — ILIKE на ``names.surname`` (не на оба поля,
      чтобы пользователь, набирающий «Иванов», не получал John/James с
      surname=None и given содержащим Иванов substring);
    * ``given`` опционален — если задан, AND ILIKE на ``names.given_name``;
    * ``year`` опционален — если задан, EXISTS на BIRT-event с
      ``date_start.year == year`` (точное совпадение года, не диапазон —
      inline-query короткий, диапазон через web-интерфейс).

    Phonetic-поиск намеренно не включён в v1: ``compute_dm_buckets``
    лежит в ``parser-service``-side и не доступен из ``telegram-bot``
    workspace member'а без переезда в ``shared-models`` (out of scope
    Phase 14.2). См. ADR-0056 §«HTTP vs direct DB» — bot читает ORM
    напрямую, поэтому HTTP-fallback на ``/trees/{id}/persons/search``
    с ``phonetic=true`` тоже не используется (для bot→parser auth
    нужно service-token, а его на existing endpoint'е нет).

    Возвращает ``(active_tree_id, hits)``. ``active_tree_id`` будет
    ``None`` если у user'а нет owned-trees — caller покажет CTA на
    создание дерева через web.
    """
    active = await fetch_active_tree(session, user_id=user_id)
    if active is None:
        return (None, [])

    surname_pattern = _escape_ilike(surname.strip()) + "%"
    name_filters: list[Any] = [
        Name.deleted_at.is_(None),
        Name.surname.ilike(surname_pattern),
    ]
    if given:
        given_pattern = _escape_ilike(given.strip()) + "%"
        name_filters.append(Name.given_name.ilike(given_pattern))

    base_filters = [
        Person.tree_id == active.id,
        Person.deleted_at.is_(None),
        Person.id.in_(select(Name.person_id).where(*name_filters)),
    ]

    if year is not None:
        base_filters.append(
            exists(
                select(Event.id)
                .join(EventParticipant, EventParticipant.event_id == Event.id)
                .where(
                    EventParticipant.person_id == Person.id,
                    Event.event_type == "BIRT",
                    Event.deleted_at.is_(None),
                    Event.date_start.is_not(None),
                    func.extract("year", Event.date_start) == year,
                )
            )
        )

    res = await session.execute(
        select(Person).where(*base_filters).order_by(Person.created_at.asc()).limit(limit)
    )
    persons = list(res.scalars().all())
    if not persons:
        return (active.id, [])

    person_ids = [p.id for p in persons]

    name_res = await session.execute(
        select(Name)
        .where(Name.person_id.in_(person_ids), Name.deleted_at.is_(None))
        .order_by(Name.person_id, Name.sort_order.asc())
    )
    primary_by_pid: dict[uuid.UUID, str] = {}
    for name in name_res.scalars().all():
        if name.person_id in primary_by_pid:
            continue
        joined = " ".join(p for p in (name.given_name, name.surname) if p).strip()
        if joined:
            primary_by_pid[name.person_id] = joined

    # Один round-trip: для каждой персоны — earliest BIRT с year + place_id.
    birth_res = await session.execute(
        select(EventParticipant.person_id, Event.date_start, Event.place_id)
        .join(Event, Event.id == EventParticipant.event_id)
        .where(
            EventParticipant.person_id.in_(person_ids),
            Event.event_type == "BIRT",
            Event.deleted_at.is_(None),
            and_(Event.date_start.is_not(None)),
        )
        .order_by(EventParticipant.person_id, Event.date_start.asc())
    )
    birth_by_pid: dict[uuid.UUID, tuple[int | None, uuid.UUID | None]] = {}
    for pid, date_start, place_id in birth_res.all():
        if pid in birth_by_pid:
            continue
        year_val = date_start.year if date_start is not None else None
        birth_by_pid[pid] = (year_val, place_id)

    # Собираем place-labels одним запросом (если есть place_id-ы).
    place_ids = [pid for _, pid in birth_by_pid.values() if pid is not None]
    place_labels: dict[uuid.UUID, str] = {}
    if place_ids:
        from shared_models.orm import Place  # noqa: PLC0415

        place_res = await session.execute(
            select(Place).where(Place.id.in_(place_ids), Place.deleted_at.is_(None))
        )
        for place in place_res.scalars().all():
            if place.canonical_name:
                place_labels[place.id] = place.canonical_name

    hits: list[InlineSearchHit] = []
    for p in persons:
        birth = birth_by_pid.get(p.id)
        hit_year: int | None = None
        place_label: str | None = None
        if birth is not None:
            hit_year, place_id = birth
            if place_id is not None:
                place_label = place_labels.get(place_id)
        hits.append(
            InlineSearchHit(
                id=p.id,
                tree_id=p.tree_id,
                primary_name=primary_by_pid.get(p.id),
                birth_year=hit_year,
                birth_place_label=place_label,
            )
        )
    return (active.id, hits)


def _escape_ilike(value: str) -> str:
    """Escape ILIKE-метасимволы (``%``/``_``) внутри пользовательского ввода.

    Иначе ``surname='%'`` матчит всё. Backslash — default-escape в
    Postgres ILIKE, явный ``ESCAPE`` clause не нужен.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def toggle_notifications(session: AsyncSession, *, tg_chat_id: int) -> tuple[bool, bool]:
    """Toggle ``notifications_enabled`` для linked-chat'а.

    Returns ``(linked, new_state)``:
    * ``linked=False`` — chat не залинкован вообще (вызывающий покажет
      «сначала /start»);
    * ``linked=True, new_state`` — текущее значение **после** toggle'а.

    Идемпотентность: caller должен интерпретировать ``new_state`` как
    «теперь ты подписан/отписан».
    """
    link = await fetch_telegram_link(session, tg_chat_id=tg_chat_id)
    if link is None:
        return (False, False)
    link.notifications_enabled = not link.notifications_enabled
    await session.flush()
    return (True, link.notifications_enabled)
