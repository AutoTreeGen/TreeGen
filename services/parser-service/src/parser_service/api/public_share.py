"""Public tree share API (Phase 11.2 — ADR-0047).

Два router'а:

* ``router_owner`` — owner-gated endpoints для управления share-link'ами.
  Mounted под ``/trees/{tree_id}/public-share`` с router-level Bearer-auth.
* ``router_public`` — public read-only ``GET /public/trees/{token}``.
  БЕЗ auth, rate-limited (60req/min per IP). DNA-данные вырезаны;
  alive-персоны анонимизированы.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from shared_models import TreeRole
from shared_models.orm import (
    Event,
    EventParticipant,
    Family,
    FamilyChild,
    Name,
    Person,
    PublicTreeShare,
    Tree,
    User,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import get_current_user
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.schemas import (
    PublicShareCreateRequest,
    PublicShareResponse,
    PublicTreeFamily,
    PublicTreePerson,
    PublicTreeResponse,
)
from parser_service.services.permissions import require_tree_role
from parser_service.utils.rate_limiter import public_share_rate_limiter

router_owner = APIRouter()
router_public = APIRouter()


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


# Person старше этого возраста (в годах) считается определённо умершим даже
# без зарегистрированного DEAT-события. Privacy-default; точная цифра не
# критична — большая часть anonymization работает по наличию DEAT-события.
_MAX_PLAUSIBLE_AGE_YEARS = 110

# Буква-маркер «alive» в публичном ответе. Не локализуется на server-side —
# UI заменит её, если будет нужна локализация (i18n живёт во frontend'е).
_ANON_DISPLAY_NAME = "Living relative"


def _generate_share_token() -> str:
    """Случайный URL-safe token (~20 chars, ~120 бит энтропии)."""
    return secrets.token_urlsafe(15)


def _build_public_url(settings: Settings, token: str) -> str:
    """Собрать shareable URL: ``${public_base_url}/public/trees/{token}``."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/public/trees/{token}"


def _to_share_response(
    share: PublicTreeShare,
    *,
    settings: Settings,
) -> PublicShareResponse:
    """ORM → DTO с готовым public_url."""
    return PublicShareResponse(
        id=share.id,
        tree_id=share.tree_id,
        share_token=share.share_token,
        public_url=_build_public_url(settings, share.share_token),
        expires_at=share.expires_at,
        created_at=share.created_at,
    )


async def _get_active_share(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
) -> PublicTreeShare | None:
    """Активный share = не revoked И не expired (на сервер-time)."""
    now = dt.datetime.now(dt.UTC)
    res = await session.execute(
        select(PublicTreeShare)
        .where(
            PublicTreeShare.tree_id == tree_id,
            PublicTreeShare.revoked_at.is_(None),
        )
        .order_by(PublicTreeShare.created_at.desc()),
    )
    for share in res.scalars().all():
        if share.expires_at is None or share.expires_at > now:
            return share
    return None


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Trust X-Forwarded-For при наличии."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return str(forwarded.split(",")[0].strip())
    if request.client is not None:
        return str(request.client.host)
    return "unknown"


# ---------------------------------------------------------------------------
# Owner endpoints.
# ---------------------------------------------------------------------------


@router_owner.post(
    "/trees/{tree_id}/public-share",
    response_model=PublicShareResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать (или вернуть существующий) public-share для дерева",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def create_public_share(
    tree_id: Annotated[uuid.UUID, Path(...)],
    body: PublicShareCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    user: Annotated[User, Depends(get_current_user)],
) -> PublicShareResponse:
    """Owner-gated. Идемпотентно: если активный share уже есть — возвращает его.

    Если активного нет (нет вообще, или revoked/expired) — создаёт новый
    с уникальным token. ``expires_in_days`` опционально устанавливает TTL.
    """
    existing = await _get_active_share(session, tree_id=tree_id)
    if existing is not None:
        return _to_share_response(existing, settings=settings)

    expires_at: dt.datetime | None = None
    if body.expires_in_days is not None:
        expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(days=body.expires_in_days)

    share = PublicTreeShare(
        tree_id=tree_id,
        share_token=_generate_share_token(),
        created_by_user_id=user.id,
        expires_at=expires_at,
    )
    session.add(share)
    await session.commit()
    await session.refresh(share)
    return _to_share_response(share, settings=settings)


@router_owner.get(
    "/trees/{tree_id}/public-share",
    response_model=PublicShareResponse | None,
    summary="Получить активный public-share или null",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def get_public_share(
    tree_id: Annotated[uuid.UUID, Path(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> PublicShareResponse | None:
    """Возвращает активный share для tree или null если нет активного."""
    share = await _get_active_share(session, tree_id=tree_id)
    if share is None:
        return None
    return _to_share_response(share, settings=settings)


@router_owner.delete(
    "/trees/{tree_id}/public-share",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke активный public-share (идемпотентно)",
    dependencies=[Depends(require_tree_role(TreeRole.OWNER))],
)
async def delete_public_share(
    tree_id: Annotated[uuid.UUID, Path(...)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Soft-revoke активного share'а. Если активного нет — 204 без эффекта."""
    share = await _get_active_share(session, tree_id=tree_id)
    if share is None:
        return
    share.revoked_at = dt.datetime.now(dt.UTC)
    await session.commit()


# ---------------------------------------------------------------------------
# Public endpoint — NO AUTH, rate-limited.
# ---------------------------------------------------------------------------


def _primary_display_name(names: list[Name]) -> str:
    """Собрать display name из первичной Name-row персоны.

    Стратегия: берём первое не-soft-deleted имя по sort_order. Если нет
    (defensive) — "Unknown".
    """
    active = [n for n in names if n.deleted_at is None]
    if not active:
        return "Unknown"
    name = sorted(active, key=lambda n: n.sort_order)[0]
    parts = [name.given_name, name.surname]
    return " ".join(p for p in parts if p) or "Unknown"


def _is_likely_alive(events: list[Event], now: dt.datetime) -> bool:
    """Эвристика: persona alive если нет DEAT-события и (birth неизвестен
    ИЛИ возраст ≤ ``_MAX_PLAUSIBLE_AGE_YEARS``).
    """
    has_death = any(e.event_type == "DEAT" for e in events)
    if has_death:
        return False
    birth_dates = [
        e.date_start for e in events if e.event_type == "BIRT" and e.date_start is not None
    ]
    if not birth_dates:
        # Нет даты рождения → privacy-default: предполагаем alive.
        return True
    earliest_birth = min(birth_dates)
    age_years = (now.date() - earliest_birth).days / 365.25
    return age_years <= _MAX_PLAUSIBLE_AGE_YEARS


def _person_to_public_dto(
    person: Person,
    names: list[Name],
    events: list[Event],
    *,
    now: dt.datetime,
) -> PublicTreePerson:
    """Применить privacy-фильтры и собрать DTO."""
    alive = _is_likely_alive(events, now)
    if alive:
        return PublicTreePerson(
            id=person.id,
            display_name=_ANON_DISPLAY_NAME,
            sex=person.sex,
            birth_year=None,
            death_year=None,
            is_anonymized=True,
        )
    birth_year: int | None = None
    death_year: int | None = None
    for e in events:
        if e.event_type == "BIRT" and e.date_start is not None and birth_year is None:
            birth_year = e.date_start.year
        elif e.event_type == "DEAT" and e.date_start is not None and death_year is None:
            death_year = e.date_start.year
    return PublicTreePerson(
        id=person.id,
        display_name=_primary_display_name(names),
        sex=person.sex,
        birth_year=birth_year,
        death_year=death_year,
        is_anonymized=False,
    )


@router_public.get(
    "/public/trees/{token}",
    response_model=PublicTreeResponse,
    summary="Public read-only вид дерева по share-token (NO AUTH, rate-limited)",
)
async def get_public_tree(
    token: Annotated[str, Path(min_length=8, max_length=32)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> PublicTreeResponse:
    """Публичный read-only вид с privacy-фильтрами.

    * Token не найден / revoked / expired → 404 (намеренно одинаковый код,
      чтобы не палить факт существования).
    * Rate limit 60req/min per IP → 429.
    * DNA-данные не возвращаются вообще.
    * Persons-likely-alive анонимизированы.
    """
    client_ip = _client_ip(request)
    if not public_share_rate_limiter.allow(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded; try again in a minute",
        )

    share = (
        await session.execute(
            select(PublicTreeShare).where(PublicTreeShare.share_token == token),
        )
    ).scalar_one_or_none()
    now = dt.datetime.now(dt.UTC)
    if (
        share is None
        or share.revoked_at is not None
        or (share.expires_at is not None and share.expires_at <= now)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    tree = await session.get(Tree, share.tree_id)
    if tree is None or tree.deleted_at is not None:
        # Сторонний кейс: tree удалили после создания share'а. Тоже 404.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")

    # Persons (живые, не soft-deleted).
    persons_res = await session.execute(
        select(Person).where(
            Person.tree_id == share.tree_id,
            Person.deleted_at.is_(None),
        ),
    )
    persons: list[Person] = list(persons_res.scalars().all())
    person_ids = [p.id for p in persons]

    # Names — selectin для избегания N+1.
    names_by_person: dict[uuid.UUID, list[Name]] = {pid: [] for pid in person_ids}
    if person_ids:
        names_res = await session.execute(
            select(Name).where(Name.person_id.in_(person_ids)),
        )
        for n in names_res.scalars().all():
            names_by_person.setdefault(n.person_id, []).append(n)

    # Events — фильтруем только BIRT/DEAT (нам только годы нужны).
    events_by_person: dict[uuid.UUID, list[Event]] = {pid: [] for pid in person_ids}
    if person_ids:
        events_res = await session.execute(
            select(Event, EventParticipant.person_id)
            .join(EventParticipant, EventParticipant.event_id == Event.id)
            .where(
                EventParticipant.person_id.in_(person_ids),
                Event.event_type.in_(("BIRT", "DEAT")),
                Event.deleted_at.is_(None),
            ),
        )
        for event, pid in events_res.all():
            events_by_person.setdefault(pid, []).append(event)

    persons_dto = [
        _person_to_public_dto(
            p,
            names_by_person.get(p.id, []),
            events_by_person.get(p.id, []),
            now=now,
        )
        for p in persons
    ]

    # Families.
    families_res = await session.execute(
        select(Family).where(
            Family.tree_id == share.tree_id,
            Family.deleted_at.is_(None),
        ),
    )
    families = list(families_res.scalars().all())
    family_ids = [f.id for f in families]
    children_by_family: dict[uuid.UUID, list[uuid.UUID]] = {fid: [] for fid in family_ids}
    if family_ids:
        fc_res = await session.execute(
            select(FamilyChild).where(FamilyChild.family_id.in_(family_ids)),
        )
        for fc in fc_res.scalars().all():
            children_by_family.setdefault(fc.family_id, []).append(fc.child_person_id)

    families_dto = [
        PublicTreeFamily(
            id=f.id,
            husband_id=f.husband_id,
            wife_id=f.wife_id,
            children_ids=children_by_family.get(f.id, []),
        )
        for f in families
    ]

    return PublicTreeResponse(
        tree_name=tree.name,
        person_count=len(persons),
        persons=persons_dto,
        families=families_dto,
    )
