"""User account settings API (Phase 4.10b → 4.11a).

Endpoints:

* ``GET    /users/me`` — current user's profile.
* ``PATCH  /users/me`` — update display_name / locale / timezone.
* ``POST   /users/me/erasure-request`` — open GDPR erasure request (Phase
  4.10b stub; Phase 4.11b/c добавит processing).
* ``POST   /users/me/export-request`` — open GDPR data-export request.
  Phase 4.11a: enqueue arq job ``run_user_export_job`` сразу после
  insert'а row.
* ``GET    /users/me/requests`` — список request'ов текущего user'а.
  Phase 4.11a: cursor-based pagination + filter по ``kind`` / ``status``,
  для ``done`` export'ов отдаём fresh signed-URL (15 мин TTL).

Phase 4.10b создавала row'ы как stub (ADR-0038). Phase 4.11a
(ADR-0046) досборала: arq worker для export, audit-log entries для
GDPR-action'ов, signed-URL re-issue на list call.

Все endpoint'ы требуют Bearer JWT (router-level в main.py); user_id
приходит из ``Depends(parser_service.auth.get_current_user_id)``.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import logging
import uuid
from typing import Annotated, Any, Literal

from arq import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from shared_models.enums import ActorKind, AuditAction
from shared_models.orm import AuditLog, User, UserActionRequest
from shared_models.storage import ObjectStorage, build_storage_from_env
from shared_models.types import new_uuid
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.auth import RequireUser
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.queue import get_arq_pool
from parser_service.services.user_export_runner import (
    build_signed_url_for_existing_export,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Pagination caps (ADR-0046 §«List endpoint»).
_LIST_DEFAULT_LIMIT: int = 20
_LIST_MAX_LIMIT: int = 50


# Storage dep — overridable из тестов через
# ``app.dependency_overrides[get_export_storage] = lambda: InMemoryStorage()``.
def get_export_storage() -> ObjectStorage:
    """Construct storage backend by env. См. shared_models.storage.

    Per-call construction намеренно — Settings/ENV могут меняться между
    тестами, и боль от чтения env на каждом list-call'е пренебрежима
    (~µs). Production-side: backend instances stateless, no leak.
    """
    return build_storage_from_env()


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
    """Один request-row для UI (``user_action_requests``).

    Phase 4.11a: добавлены ``signed_url`` + ``signed_url_expires_at`` —
    выставляются только для ``kind='export'`` со ``status='done'``.
    Каждый list-call даёт fresh URL (storage.signed_download_url —
    pure-function от key + expires).
    """

    id: uuid.UUID
    kind: Literal["export", "erasure"]
    status: Literal["pending", "processing", "done", "failed", "cancelled"]
    created_at: Any
    processed_at: Any = None
    error: str | None = None
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    # Phase 4.11a additions — none-by-default для backwards-compat
    # с PR #122 frontend tests.
    signed_url: str | None = None
    signed_url_expires_at: Any = None

    model_config = ConfigDict(from_attributes=True)


class UserActionRequestsListResponse(BaseModel):
    """Ответ ``GET /users/me/requests``.

    Phase 4.11a добавил cursor pagination:

    * ``next_cursor`` — opaque token (base64) для следующей страницы.
      ``None`` означает «больше нет».
    * Caller передаёт его обратно как ``?cursor=`` параметр.
    """

    user_id: uuid.UUID
    items: list[UserActionRequestResponse]
    next_cursor: str | None = None


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
    summary="Open a GDPR erasure request (stub — processed in Phase 4.11b/c)",
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
      processing). Не множим — один request живёт до Phase 4.11b/c.
    * 202 Accepted на success. Phase 4.11a записывает audit-entry
      ``ERASURE_REQUESTED``. Реальный hard-delete cascade — Phase 4.11c.
    """
    user = await _load_user_or_500(session, user_id)
    if body.confirm_email.lower() != user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="confirm_email does not match the authenticated user's email",
        )
    await _ensure_no_active_request(session, user_id=user_id, kind="erasure")
    response = await _create_action_request(
        session,
        user_id=user_id,
        kind="erasure",
        request_metadata={"confirm_email_hash_marker": "set"},
    )
    _add_user_action_audit(
        session,
        user_id=user_id,
        request_id=response.request_id,
        action=AuditAction.ERASURE_REQUESTED,
        metadata={
            "confirm_email_match": True,
            "user_email_lower": user.email.lower(),
        },
    )
    await session.commit()
    return response


@router.post(
    "/users/me/export-request",
    response_model=ActionRequestCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Open a GDPR data-export request (Phase 4.11a)",
)
async def request_export(
    _body: ExportRequestBody,
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
) -> ActionRequestCreatedResponse:
    """Создать ``user_action_requests`` row + enqueue export job.

    409 если уже есть active export request. 202 Accepted на success —
    arq-worker заберёт job и выполнит pipeline (см.
    ``parser_service.services.user_export_runner``). Audit-entry
    ``EXPORT_REQUESTED`` записывается в ту же транзакцию что и row.
    """
    await _ensure_no_active_request(session, user_id=user_id, kind="export")
    response = await _create_action_request(
        session,
        user_id=user_id,
        kind="export",
        request_metadata={"format": "zip_v1"},
    )
    _add_user_action_audit(
        session,
        user_id=user_id,
        request_id=response.request_id,
        action=AuditAction.EXPORT_REQUESTED,
        metadata={"format": "zip_v1"},
    )
    await session.commit()

    # Enqueue после commit'а — worker увидит row в БД. deduplication_key
    # защищает от двойного enqueue если caller случайно retry'нул POST
    # (на уровне application _ensure_no_active_request это уже ловит,
    # но это defense in depth).
    await pool.enqueue_job(
        "run_user_export_job",
        str(response.request_id),
        _job_id=f"export:{response.request_id}",
    )
    return response


@router.get(
    "/users/me/requests",
    response_model=UserActionRequestsListResponse,
    summary="List the current user's GDPR action requests",
)
async def list_my_requests(
    user_id: RequireUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[ObjectStorage, Depends(get_export_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    kind: Annotated[
        Literal["export", "erasure"] | None,
        Query(description="Фильтр по типу запроса."),
    ] = None,
    status_filter: Annotated[
        Literal["pending", "processing", "done", "failed", "cancelled"] | None,
        Query(
            alias="status",
            description="Фильтр по статусу. Без значения — без фильтра.",
        ),
    ] = None,
    cursor: Annotated[
        str | None,
        Query(description="Opaque cursor token из предыдущего ответа."),
    ] = None,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=_LIST_MAX_LIMIT,
            description=f"Размер страницы (1..{_LIST_MAX_LIMIT}, дефолт {_LIST_DEFAULT_LIMIT}).",
        ),
    ] = _LIST_DEFAULT_LIMIT,
) -> UserActionRequestsListResponse:
    """Cursor-paginated список own action-request'ов.

    Изоляция: WHERE user_id = $current — другие user'ы не утекают даже
    при сбое auth.

    Sort: ``(created_at DESC, id DESC)`` — стабильный tiebreaker по UUID
    для совпадающих created_at (миллисекундное разрешение даёт коллизии
    редко, но cursor-based pagination корректность требует deterministic).

    Cursor: opaque base64-encoded ``"<created_at_iso>|<id>"``. Caller
    его не парсит — просто пересылает обратно. На next-page применяем
    keyset condition ``(created_at, id) < (cursor_ts, cursor_id)``.

    Для каждого ``done`` export-request'а issue fresh signed-URL
    (15 мин TTL по дефолту). Failure storage'а на signed-URL → URL
    остаётся ``None`` (но row отдаём — user видит что export готов,
    может re-list через UI).
    """
    cursor_ts, cursor_id = _decode_cursor(cursor)

    base_query = select(UserActionRequest).where(UserActionRequest.user_id == user_id)
    if kind is not None:
        base_query = base_query.where(UserActionRequest.kind == kind)
    if status_filter is not None:
        base_query = base_query.where(UserActionRequest.status == status_filter)
    if cursor_ts is not None and cursor_id is not None:
        # Keyset: (created_at, id) < (cursor_ts, cursor_id) при DESC-sort.
        base_query = base_query.where(
            or_(
                UserActionRequest.created_at < cursor_ts,
                and_(
                    UserActionRequest.created_at == cursor_ts,
                    UserActionRequest.id < cursor_id,
                ),
            )
        )

    # Запрашиваем limit+1 — последний элемент означает «есть next page».
    page_query = base_query.order_by(
        UserActionRequest.created_at.desc(),
        UserActionRequest.id.desc(),
    ).limit(limit + 1)
    rows = (await session.execute(page_query)).scalars().all()

    has_more = len(rows) > limit
    visible_rows = list(rows[:limit])

    items: list[UserActionRequestResponse] = []
    for row in visible_rows:
        item = UserActionRequestResponse.model_validate(row)
        if row.kind == "export" and row.status == "done":
            try:
                signed = await build_signed_url_for_existing_export(
                    row, storage=storage, settings=settings
                )
            except Exception:
                # Storage failure не должен блокировать list — отдаём row
                # без signed_url; user может re-list через минуту.
                logger.exception("Failed to issue signed URL for export %s", row.id)
                signed = None
            if signed is not None:
                item.signed_url = signed.url
                item.signed_url_expires_at = signed.expires_at
        items.append(item)

    next_cursor: str | None = None
    if has_more and visible_rows:
        last = visible_rows[-1]
        next_cursor = _encode_cursor(last.created_at, last.id)

    return UserActionRequestsListResponse(
        user_id=user_id,
        items=items,
        next_cursor=next_cursor,
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
    """Insert pending row and return the 202 payload.

    Phase 4.11a: больше не делает commit — caller responsible. Это даёт
    возможность ``request_export`` / ``request_erasure`` записать
    audit-entry в ту же транзакцию что и сам row.
    """
    row = UserActionRequest(
        user_id=user_id,
        kind=kind,
        status="pending",
        request_metadata=request_metadata,
    )
    session.add(row)
    await session.flush()
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


def _add_user_action_audit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    request_id: uuid.UUID,
    action: AuditAction,
    metadata: dict[str, Any],
) -> None:
    """Inline audit-entry для GDPR user-action.

    Дублирует структуру ``user_export_runner._build_user_action_audit``
    (worker-side), но не импортируем чтобы не тащить ZIP-pipeline в
    HTTP-слой. Конвенция совпадает: tree_id=None, actor_kind=USER,
    entity_type='user_action_request', entity_id=request_id, action.value.
    """
    session.add(
        AuditLog(
            id=new_uuid(),
            tree_id=None,
            entity_type="user_action_request",
            entity_id=request_id,
            action=action.value,
            actor_user_id=user_id,
            actor_kind=ActorKind.USER.value,
            import_job_id=None,
            reason=None,
            diff={"action": action.value, "metadata": metadata},
            created_at=dt.datetime.now(dt.UTC),
        )
    )


# ---------------------------------------------------------------------------
# Cursor pagination helpers
# ---------------------------------------------------------------------------


def _encode_cursor(created_at: dt.datetime, row_id: uuid.UUID) -> str:
    """``(timestamp, uuid)`` → opaque base64 token.

    Формат внутри: ``"<created_at_iso>|<uuid_str>"``. ISO-8601 с
    UTC timezone — детерминирован, обратно парсится через
    ``dt.datetime.fromisoformat``. base64-urlsafe-encode чтобы token
    был safe для URL без percent-escaping.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=dt.UTC)
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str | None) -> tuple[dt.datetime | None, uuid.UUID | None]:
    """Decode opaque cursor → ``(timestamp, uuid)`` или ``(None, None)``.

    На invalid token возвращаем ``(None, None)`` (как будто cursor не
    был передан) + 422. Альтернатива — silent-ignore — даёт пользователю
    путать «начало списка» с corrupted-token; явный 422 лучше.
    """
    if not token:
        return None, None
    try:
        # Восстанавливаем padding (urlsafe_b64encode стрипает '=').
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts_str, id_str = raw.split("|", maxsplit=1)
        return dt.datetime.fromisoformat(ts_str), uuid.UUID(id_str)
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid cursor token: {exc}",
        ) from exc


__all__ = ["get_export_storage", "router"]
