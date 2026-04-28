"""User account settings API (Phase 4.10b, ADR-0038).

Endpoints:

* ``GET    /users/me`` — current user's profile.
* ``PATCH  /users/me`` — update display_name / locale / timezone.
* ``POST   /users/me/erasure-request`` — open GDPR erasure request (stub).
* ``POST   /users/me/export-request`` — open data export request (stub).
* ``GET    /users/me/requests`` — list user's own action requests.

Phase 4.10b — UI contract + DB row creation. Phase 4.11 (Agent 5)
processes the ``user_action_requests`` rows (worker, file generation
для export, hard-delete cascade для erasure). См. ADR-0038.

Все endpoint'ы требуют Bearer JWT (router-level в main.py); user_id
приходит из ``Depends(parser_service.auth.get_current_user_id)``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from shared_models.orm import User, UserActionRequest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import RequireUser
from parser_service.database import get_session

logger = logging.getLogger(__name__)

router = APIRouter()


# Whitelist допустимых ``locale`` значений. Frontend i18n bundles —
# ``en`` / ``ru``; добавление новых locale = новый bundle + допуск
# здесь. Хранятся как text, не postgres ENUM (см. shared_models.enums
# §«как text, не ENUM»).
_ALLOWED_LOCALES: frozenset[str] = frozenset({"en", "ru"})


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class UserMeResponse(BaseModel):
    """Профиль текущего user'а (``GET /users/me``).

    ``email`` хранится как plain ``str`` — мы доверяем DB-row'у (он был
    провалидирован на write-боку, и dev-окружение использует
    ``.test``-TLD'ы которые EmailStr отклоняет).
    """

    id: uuid.UUID
    email: str
    clerk_user_id: str | None = None
    display_name: str | None = None
    locale: str
    timezone: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserMeUpdateRequest(BaseModel):
    """Тело ``PATCH /users/me`` — все поля опциональные.

    ``locale`` валидируется против whitelist'а на эндпоинте
    (см. ``_ALLOWED_LOCALES``). ``display_name`` обрезается до
    255 символов на DB-уровне (column type), здесь дополнительно
    обрезаем whitespace.
    """

    display_name: str | None = Field(default=None, max_length=255)
    locale: str | None = Field(default=None, max_length=8)
    timezone: str | None = Field(default=None, max_length=64)

    model_config = ConfigDict(extra="forbid")


class UserActionRequestResponse(BaseModel):
    """Один request-row для UI (``user_action_requests``)."""

    id: uuid.UUID
    kind: Literal["export", "erasure"]
    status: Literal["pending", "processing", "done", "failed", "cancelled"]
    created_at: Any
    processed_at: Any = None
    error: str | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class UserActionRequestsListResponse(BaseModel):
    """Ответ ``GET /users/me/requests``."""

    user_id: uuid.UUID
    items: list[UserActionRequestResponse]


class ErasureRequestBody(BaseModel):
    """Тело ``POST /users/me/erasure-request``.

    ``confirm_email`` — пользователь должен ввести свой email для
    подтверждения. Сравнивается case-insensitive с ``users.email``.
    Это soft-confirm: реальный hard-delete делается Phase 4.11
    воркером с дополнительным email-link confirmation.

    Валидация формата email — лёгкая (наличие ``@``); строгая
    EmailStr-валидация дала бы false-positive'ы на ``.test``-TLD'ах
    дев-окружения, а сама проверка ОВН — equality с ``users.email``.
    """

    confirm_email: str = Field(min_length=3, pattern=r".+@.+")

    model_config = ConfigDict(extra="forbid")


class ExportRequestBody(BaseModel):
    """Тело ``POST /users/me/export-request``.

    Для Phase 4.10b — пустое body (по дефолту export всех данных).
    Phase 4.11 расширит filter'ами (subset of trees, дата-cutoff).
    """

    model_config = ConfigDict(extra="forbid")


class ActionRequestCreatedResponse(BaseModel):
    """202 Accepted ответ на ``erasure-request`` / ``export-request``."""

    request_id: uuid.UUID
    kind: Literal["export", "erasure"]
    status: Literal["pending"]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/users/me",
    response_model=UserMeResponse,
    summary="Get the current user's profile",
)
async def get_me(
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserMeResponse:
    """Прочитать ``users``-row текущего authenticated user'а."""
    user = await _load_user_or_500(session, user_id)
    return UserMeResponse.model_validate(user)


@router.patch(
    "/users/me",
    response_model=UserMeResponse,
    summary="Update display_name / locale / timezone",
)
async def patch_me(
    body: UserMeUpdateRequest,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserMeResponse:
    """Обновить редактируемые поля профиля.

    * ``locale`` — whitelist {"en", "ru"}; 422 на неизвестный.
    * ``display_name`` — пустая строка после strip → ``NULL``
      (semantically «снять имя»).
    * ``timezone`` — IANA-string; здесь не валидируем content (frontend
      шлёт только из known-list), хранится как-есть.
    """
    user = await _load_user_or_500(session, user_id)

    if body.locale is not None:
        if body.locale not in _ALLOWED_LOCALES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unsupported locale {body.locale!r}; allowed: {sorted(_ALLOWED_LOCALES)}",
            )
        user.locale = body.locale

    if body.display_name is not None:
        cleaned = body.display_name.strip()
        user.display_name = cleaned or None

    if body.timezone is not None:
        cleaned_tz = body.timezone.strip()
        user.timezone = cleaned_tz or None

    await session.flush()
    await session.commit()
    return UserMeResponse.model_validate(user)


@router.post(
    "/users/me/erasure-request",
    response_model=ActionRequestCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Open a GDPR erasure request (stub — processed in Phase 4.11)",
)
async def request_erasure(
    body: ErasureRequestBody,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActionRequestCreatedResponse:
    """Создать ``user_action_requests`` row с ``kind='erasure'``.

    * 422 если ``confirm_email`` не совпадает с user.email
      (case-insensitive).
    * 409 если у user'а уже есть active erasure request (pending или
      processing). Не множим — один request живёт до Phase 4.11
      processing'а.
    * 202 Accepted на success — обработка идёт асинхронно. Phase 4.11
      добавит worker.
    """
    user = await _load_user_or_500(session, user_id)
    if body.confirm_email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confirm_email does not match the authenticated user's email",
        )
    await _ensure_no_active_request(session, user_id=user_id, kind="erasure")
    return await _create_action_request(
        session,
        user_id=user_id,
        kind="erasure",
        request_metadata={"confirm_email_hash_marker": "set"},
    )


@router.post(
    "/users/me/export-request",
    response_model=ActionRequestCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Open a data-export request (stub — processed in Phase 4.11)",
)
async def request_export(
    _body: ExportRequestBody,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ActionRequestCreatedResponse:
    """Создать ``user_action_requests`` row с ``kind='export'``.

    409 если уже есть active export request. 202 Accepted на success.
    """
    await _ensure_no_active_request(session, user_id=user_id, kind="export")
    return await _create_action_request(
        session,
        user_id=user_id,
        kind="export",
        request_metadata={"format": "gedcom_tar_gz"},
    )


@router.get(
    "/users/me/requests",
    response_model=UserActionRequestsListResponse,
    summary="List the current user's pending/processed action requests",
)
async def list_my_requests(
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserActionRequestsListResponse:
    """Вернуть все ``user_action_requests`` текущего user'а (any status).

    Изоляция: WHERE user_id = $current; других user'ов не утечь даже
    если что-то сломалось в auth.
    """
    rows = (
        (
            await session.execute(
                select(UserActionRequest)
                .where(UserActionRequest.user_id == user_id)
                .order_by(UserActionRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return UserActionRequestsListResponse(
        user_id=user_id,
        items=[UserActionRequestResponse.model_validate(r) for r in rows],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_user_or_500(session: AsyncSession, user_id: uuid.UUID) -> User:
    """Load ``User``-row by id; 500 if missing (auth-dep guarantees row)."""
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        # Не должно происходить — RequireUser JIT-create гарантирует row.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authenticated user row not found",
        )
    return user


async def _ensure_no_active_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
) -> None:
    """409 if user already has a pending/processing request for ``kind``."""
    existing = (
        await session.execute(
            select(UserActionRequest.id).where(
                UserActionRequest.user_id == user_id,
                UserActionRequest.kind == kind,
                UserActionRequest.status.in_(("pending", "processing")),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An active {kind} request already exists (id={existing}).",
        )


async def _create_action_request(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    request_metadata: dict[str, Any],
) -> ActionRequestCreatedResponse:
    """Insert pending row and return the 202 payload."""
    row = UserActionRequest(
        user_id=user_id,
        kind=kind,
        status="pending",
        request_metadata=request_metadata,
    )
    session.add(row)
    await session.flush()
    await session.commit()
    logger.info(
        "user_action_request created: user_id=%s kind=%s id=%s",
        user_id,
        kind,
        row.id,
    )
    return ActionRequestCreatedResponse(
        request_id=row.id,
        kind=kind,
        status="pending",
    )


__all__ = ["router"]
