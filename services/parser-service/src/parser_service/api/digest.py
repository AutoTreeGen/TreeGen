"""Internal digest-summary endpoint (Phase 14.2).

``GET /users/{user_id}/digest-summary?since=<iso8601>`` — internal endpoint,
который дёргается из ``services/telegram-bot`` weekly-digest worker'а для
сборки еженедельного отчёта по дереву.

Auth — service-token через ``X-Internal-Service-Token`` header (зеркало
``services/telegram-bot/api/notify.py``). Этот endpoint **не** покрыт
Clerk-JWT router-level dependency (см. main.py): bot-сервис не имеет
Clerk-юзера и шлёт shared-secret. Токен пустой → 503 (fail-safe).

Контракт:

* считаем persons и hypotheses по всем ``owner_user_id == user_id``
  деревьям (member-trees из Phase 11.0 sharing — out-of-scope здесь);
* ``new_persons_count`` — persons.created_at > since в любом owned-дереве;
* ``new_hypotheses_pending`` — текущий счёт ``reviewed_status == 'pending'``
  по owned-деревьям (snapshot, не «новые за неделю» — спека digest'а:
  «3 гипотезы ждут проверки»);
* ``top_3_recent_persons`` — три самых новых persons (created_at desc),
  один primary_name на персону (по sort_order ASC).
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from shared_models.enums import HypothesisReviewStatus
from shared_models.orm import Hypothesis, Name, Person, Tree
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.config import Settings, get_settings
from parser_service.database import get_session

router = APIRouter()
_LOG: Final = logging.getLogger(__name__)


class DigestPersonCard(BaseModel):
    """Карточка персоны для digest top-3."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tree_id: uuid.UUID
    primary_name: str | None = Field(
        description="``given_name + ' ' + surname`` по первому Name (sort_order ASC). "
        "None если у персоны нет ни одного имени.",
    )
    birth_year: int | None = Field(
        default=None,
        description="Год BIRT-события если есть; иначе None.",
    )


class DigestSummaryResponse(BaseModel):
    """Тело ответа ``/users/{id}/digest-summary``."""

    user_id: uuid.UUID
    since: dt.datetime = Field(description="Нижняя граница окна (inclusive).")
    new_persons_count: int = Field(ge=0)
    new_hypotheses_pending: int = Field(ge=0)
    top_3_recent_persons: list[DigestPersonCard] = Field(
        description="До 3-х самых новых persons across owned-trees, по created_at DESC.",
    )


def _verify_service_token(
    settings: Settings,
    provided: str | None,
) -> None:
    """Constant-time check ``X-Internal-Service-Token``.

    503 если токен не настроен в env (fail-safe от misconfigured-окружения,
    которое иначе принимало бы любой запрос). 401 если токен предоставлен
    неправильно либо отсутствует.
    """
    if not settings.internal_service_token:
        _LOG.error("internal_service_token not configured — refusing /digest-summary")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal service token not configured",
        )
    if provided is None or not hmac.compare_digest(
        provided,
        settings.internal_service_token,
    ):
        # Без detail-shape, чтобы не подсказывать атакующему валидный
        # формат (см. notify.py).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service token",
        )


@router.get(
    "/users/{user_id}/digest-summary",
    response_model=DigestSummaryResponse,
    summary="Internal: weekly digest aggregation for one user (service-token auth)",
)
async def get_digest_summary(
    user_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    since: Annotated[
        dt.datetime,
        Query(
            description=(
                "Нижняя граница окна (inclusive, ISO-8601 datetime). Worker шлёт "
                "``now() - 7 days``. Время трактуется как UTC если без tzinfo."
            ),
        ),
    ],
    x_internal_service_token: Annotated[str | None, Header()] = None,
) -> DigestSummaryResponse:
    """Aggregate digest counters + top-3 recent persons for ``user_id``.

    Endpoint internal: вызывается только из telegram-bot digest worker'а.
    """
    _verify_service_token(settings, x_internal_service_token)

    # Datetime без tzinfo считаем как UTC — чтобы worker'у не надо было
    # форматировать ``+00:00`` в URL'е (FastAPI принимает оба варианта).
    if since.tzinfo is None:
        since = since.replace(tzinfo=dt.UTC)

    owned_tree_ids_subq = select(Tree.id).where(
        Tree.owner_user_id == user_id,
        Tree.deleted_at.is_(None),
    )

    new_persons_count = await session.scalar(
        select(func.count(Person.id)).where(
            Person.tree_id.in_(owned_tree_ids_subq),
            Person.deleted_at.is_(None),
            Person.created_at >= since,
        )
    )

    new_hypotheses_pending = await session.scalar(
        select(func.count(Hypothesis.id)).where(
            Hypothesis.tree_id.in_(owned_tree_ids_subq),
            Hypothesis.deleted_at.is_(None),
            Hypothesis.reviewed_status == HypothesisReviewStatus.PENDING.value,
        )
    )

    # Top-3 recent persons across owned trees.
    top_persons_res = await session.execute(
        select(Person)
        .where(
            Person.tree_id.in_(owned_tree_ids_subq),
            Person.deleted_at.is_(None),
            Person.created_at >= since,
        )
        .order_by(Person.created_at.desc())
        .limit(3)
    )
    top_persons = list(top_persons_res.scalars().all())

    person_ids = [p.id for p in top_persons]
    primary_by_pid: dict[uuid.UUID, str] = {}
    if person_ids:
        names_res = await session.execute(
            select(Name)
            .where(Name.person_id.in_(person_ids), Name.deleted_at.is_(None))
            .order_by(Name.person_id, Name.sort_order.asc())
        )
        for name in names_res.scalars().all():
            if name.person_id in primary_by_pid:
                continue
            joined = " ".join(p for p in (name.given_name, name.surname) if p).strip()
            if joined:
                primary_by_pid[name.person_id] = joined

    cards = [
        DigestPersonCard(
            id=p.id,
            tree_id=p.tree_id,
            primary_name=primary_by_pid.get(p.id),
            birth_year=None,
        )
        for p in top_persons
    ]

    return DigestSummaryResponse(
        user_id=user_id,
        since=since,
        new_persons_count=int(new_persons_count or 0),
        new_hypotheses_pending=int(new_hypotheses_pending or 0),
        top_3_recent_persons=cards,
    )
