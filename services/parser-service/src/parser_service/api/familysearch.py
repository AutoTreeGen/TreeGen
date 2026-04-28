"""FamilySearch import API (Phase 5.0 → 5.1).

Эндпоинты делятся на три группы:

1. **Stateless импорт** (Phase 5.0 / 5.1 baseline):
   * ``POST /imports/familysearch`` — синхронный импорт с
     ``access_token`` в body. Используется тестами и backwards-compat
     CLI; не требует подключённого FS-аккаунта.
   * ``GET /imports/familysearch/{job_id}`` — sugar-endpoint
     для polling FS-jobs.

2. **Server-side OAuth flow** (Phase 5.1, ADR-0027):
   * ``GET /imports/familysearch/oauth/start`` — отдаёт authorize URL
     и кладёт state в Redis с TTL.
   * ``GET /imports/familysearch/oauth/callback`` — принимает code+state,
     обменивает на токен, шифрует Fernet'ом и пишет в
     ``users.fs_token_encrypted``.
   * ``DELETE /imports/familysearch/disconnect`` — обнуляет колонку.
   * ``GET /imports/familysearch/me`` — статус подключения (без токена!).

3. **Asynchronous импорт через arq** (Phase 5.1):
   * ``GET /imports/familysearch/pedigree/preview`` — read-only сводка
     pedigree (count + sample names), без impórt-job'а.
   * ``POST /imports/familysearch/import`` — создаёт ``ImportJob`` и
     enqueue'ит ``run_fs_import_job`` в arq. Использует токен из БД,
     **не** требует ``access_token`` в body. UI подписывается на SSE
     по ``/imports/{job_id}/events``.

См. ADR-0011 (клиент), ADR-0017 (маппинг), ADR-0027 (хранилище токена).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import secrets
import uuid
from typing import Annotated

import httpx
import redis.asyncio as redis_asyncio
from arq.connections import ArqRedis
from familysearch_client import (
    AuthError,
    AuthorizationRequest,
    ClientError,
    FamilySearchAuth,
    FamilySearchClient,
    FamilySearchConfig,
    RateLimitError,
    ServerError,
    Token,
)
from familysearch_client import (
    NotFoundError as FsNotFoundError,
)
from familysearch_client.models import FsPerson
from fastapi import (
    APIRouter,
    Cookie,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from shared_models.enums import (
    ImportJobStatus,
    ImportSourceKind,
)
from shared_models.orm import ImportJob, Tree, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from parser_service.billing import require_feature
from parser_service.config import Settings, get_settings
from parser_service.database import get_session
from parser_service.fs_oauth import (
    FsStoredToken,
    OAuthStateRecord,
    TokenCryptoError,
    TokenStorage,
    consume_state,
    get_token_storage,
    is_fs_token_storage_configured,
    save_state,
)
from parser_service.queue import get_arq_pool
from parser_service.schemas import (
    FamilySearchAccountInfo,
    FamilySearchAsyncImportRequest,
    FamilySearchImportRequest,
    FamilySearchImportResponse,
    FamilySearchOAuthStartResponse,
    FamilySearchPedigreePreviewPerson,
    FamilySearchPedigreePreviewResponse,
    ImportJobResponse,
)
from parser_service.services.familysearch_importer import import_fs_pedigree
from parser_service.services.metrics import import_completed_total

logger = logging.getLogger(__name__)

router = APIRouter()

# Имя arq-функции для async-импорта FS pedigree. Регистрируется в
# worker.py (см. WorkerSettings.functions). Захардкожено строкой,
# чтобы избежать import cycle worker ↔ api.
RUN_FS_IMPORT_JOB_NAME = "run_fs_import_job"

_EVENTS_URL_TEMPLATE = "/imports/{job_id}/events"

# Длина «sample» preview списка персон. UI показывает первые N для
# визуальной сверки, а полный count приходит отдельным полем.
_PREVIEW_SAMPLE_LIMIT = 10

# Cookie с CSRF-state, выставляется при oauth/start, проверяется при callback.
_OAUTH_STATE_COOKIE = "fs_oauth_state"


def _token_fingerprint(access_token: str) -> str:
    """sha256(access_token)[:8] — для логов без раскрытия секрета."""
    return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:8]


def _events_url(job_id: uuid.UUID) -> str:
    """Относительный URL SSE-эндпоинта прогресса импорта."""
    return _EVENTS_URL_TEMPLATE.format(job_id=job_id)


def _fs_config_for(settings: Settings) -> FamilySearchConfig:
    """Sandbox/production FamilySearchConfig, выбирается ENV-параметром."""
    if settings.fs_environment == "production":
        return FamilySearchConfig.production()
    return FamilySearchConfig.sandbox()


# Хук для тестов (тот же паттерн, что в imports_sse._redis_client_factory):
# pytest подменяет фабрику, чтобы вернуть fakeredis вместо реального Redis.
# Module-level binding — потому что Depends-граф для эндпоинтов callback'а
# срабатывает на raw FastAPI app, а не на TestClient overrides внутри
# вспомогательных утилит.
_redis_client_factory = None


def _make_redis_client(settings: Settings) -> redis_asyncio.Redis:
    """Создать async Redis-клиент. Тестовый хук — подменить _redis_client_factory."""
    if _redis_client_factory is not None:
        client: redis_asyncio.Redis = _redis_client_factory()
        return client
    return redis_asyncio.Redis.from_url(settings.redis_url, decode_responses=True)


def _require_token_storage(settings: Settings) -> TokenStorage:
    """Получить :class:`TokenStorage` или 503, если ENV не настроен.

    Все эндпоинты server-side OAuth flow требуют валидный
    ``PARSER_SERVICE_FS_TOKEN_KEY``; без него возвращаем 503 с понятным
    сообщением, а не 500 ImportError.
    """
    if not is_fs_token_storage_configured(settings.fs_token_key):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "FamilySearch OAuth not configured: PARSER_SERVICE_FS_TOKEN_KEY "
                "is missing or not a valid Fernet key."
            ),
        )
    try:
        return get_token_storage(settings.fs_token_key)
    except TokenCryptoError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FamilySearch OAuth misconfigured: {e}",
        ) from e


def _require_client_id(settings: Settings) -> str:
    """Вернуть FS client_id или 503."""
    if not settings.fs_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("FamilySearch OAuth not configured: PARSER_SERVICE_FS_CLIENT_ID is empty."),
        )
    return settings.fs_client_id


async def _ensure_owner(session: AsyncSession, email: str) -> User:
    """Найти/создать User по email (см. api.imports._ensure_owner — тот же контракт).

    Phase 5.1 живёт без полноценного auth-middleware; владелец берётся
    из ``settings.owner_email``. После Phase 4.x этот хелпер уйдёт, и user
    будет приходить из request.state.
    """
    res = await session.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        external_auth_id=f"local:{email}",
        display_name=email.split("@", maxsplit=1)[0],
        locale="en",
    )
    session.add(user)
    await session.flush()
    return user


def _stored_token_from_oauth(token: Token, *, fs_user_id: str | None) -> FsStoredToken:
    """Превратить :class:`Token` (из FamilySearchAuth) в :class:`FsStoredToken`.

    ``expires_at`` высчитывается как ``now + expires_in``. С 60-секундным
    запасом считаем «протух» — см. :meth:`FsStoredToken.is_expired`.
    """
    now = dt.datetime.now(dt.UTC)
    return FsStoredToken(
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=now + dt.timedelta(seconds=max(token.expires_in, 0)),
        scope=token.scope,
        fs_user_id=fs_user_id,
        stored_at=now,
    )


# ============================================================================
# Stateless импорт (legacy / sync) — Phase 5.0 baseline.
# ============================================================================


@router.post(
    "",
    response_model=FamilySearchImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Импортировать FamilySearch person + N поколений предков (sync)",
)
async def create_familysearch_import(
    request: FamilySearchImportRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    # Phase 12.0: FS-импорт доступен только на Pro-плане.
    _entitlement: Annotated[None, require_feature("fs_import_enabled")] = None,
) -> FamilySearchImportResponse:
    """Импорт FS pedigree в существующее дерево (синхронный, stateless).

    Маппинг FS GEDCOM-X → ORM — см. ADR-0017. Идемпотентность:
    повторный запрос с тем же ``fs_person_id`` обновит существующих
    persons, не создаст дубликаты.

    Pro-only: см. ADR-0034 §«Plan limits».
    """
    tree = (
        await session.execute(select(Tree).where(Tree.id == request.tree_id))
    ).scalar_one_or_none()
    if tree is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {request.tree_id} not found",
        )

    logger.info(
        "FS import (sync) requested: fs_person_id=%s tree_id=%s generations=%d token_fp=%s",
        request.fs_person_id,
        request.tree_id,
        request.generations,
        _token_fingerprint(request.access_token),
    )

    try:
        job = await import_fs_pedigree(
            session,
            access_token=request.access_token,
            fs_person_id=request.fs_person_id,
            tree_id=request.tree_id,
            owner_user_id=tree.owner_user_id,
            generations=request.generations,
        )
    except FsNotFoundError as e:
        import_completed_total.labels(source="fs", outcome="error").inc()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FamilySearch person {request.fs_person_id} not found",
        ) from e
    except AuthError as e:
        import_completed_total.labels(source="fs", outcome="error").inc()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"FamilySearch rejected access token: {e}",
        ) from e
    except RateLimitError as e:
        retry_after = int(e.retry_after) if e.retry_after is not None else None
        headers: dict[str, str] = {}
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        import_completed_total.labels(source="fs", outcome="error").inc()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="FamilySearch rate limit exceeded",
            headers=headers,
        ) from e
    except ServerError as e:
        import_completed_total.labels(source="fs", outcome="error").inc()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FamilySearch upstream error: {e}",
        ) from e
    except ClientError as e:
        import_completed_total.labels(source="fs", outcome="error").inc()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FamilySearch client error: {e}",
        ) from e

    base = ImportJobResponse.model_validate(job)
    fs_attempts = base.stats.get("fs_dedup_attempts_created", 0) if base.stats else 0
    review_url = f"/trees/{request.tree_id}/dedup-attempts" if fs_attempts else None
    return FamilySearchImportResponse(
        **base.model_dump(),
        review_url=review_url,
    )


# ============================================================================
# Server-side OAuth flow (Phase 5.1, ADR-0027).
# ============================================================================


@router.get(
    "/oauth/start",
    response_model=FamilySearchOAuthStartResponse,
    summary="Подготовить authorize URL FamilySearch (PKCE + CSRF-state)",
)
async def oauth_start(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FamilySearchOAuthStartResponse:
    """Сгенерировать OAuth Authorize URL и положить state в Redis.

    Нет ``user_id`` в API: Phase 5.1 живёт без auth-middleware, владелец
    резолвится из ``settings.owner_email``. После Phase 4.x этот хелпер
    уйдёт.
    """
    _require_token_storage(settings)
    client_id = _require_client_id(settings)

    user = await _ensure_owner(session, settings.owner_email)

    auth = FamilySearchAuth(client_id=client_id, config=_fs_config_for(settings))
    auth_request: AuthorizationRequest = auth.start_flow(
        redirect_uri=settings.fs_oauth_redirect_uri,
        scope=settings.fs_oauth_scope,
    )

    record = OAuthStateRecord(
        state=auth_request.state,
        code_verifier=auth_request.code_verifier,
        user_id=user.id,
        redirect_uri=settings.fs_oauth_redirect_uri,
        scope=settings.fs_oauth_scope,
    )
    redis_client = _make_redis_client(settings)
    try:
        await save_state(redis_client, record, ttl_seconds=settings.fs_oauth_state_ttl)
    finally:
        await redis_client.aclose()

    # CSRF cookie: HttpOnly чтобы JS не мог прочитать. Secure только под
    # https; в dev http://localhost — оставляем False (иначе браузер не
    # положит cookie). SameSite=Lax — стандартный для OAuth-callback'ов.
    response.set_cookie(
        key=_OAUTH_STATE_COOKIE,
        value=auth_request.state,
        max_age=settings.fs_oauth_state_ttl,
        httponly=True,
        secure=settings.fs_oauth_redirect_uri.startswith("https://"),
        samesite="lax",
    )
    return FamilySearchOAuthStartResponse(
        authorize_url=auth_request.authorize_url,
        state=auth_request.state,
        expires_in=settings.fs_oauth_state_ttl,
    )


@router.get(
    "/oauth/callback",
    summary="OAuth callback FamilySearch — обменять code на токен и сохранить",
)
async def oauth_callback(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    code: Annotated[str | None, Query(description="Authorization code от FS.")] = None,
    state: Annotated[
        str | None, Query(description="CSRF state, тот же что в /oauth/start.")
    ] = None,
    error: Annotated[
        str | None, Query(description="Если user отклонил, FS присылает ?error=...")
    ] = None,
    state_cookie: Annotated[str | None, Cookie(alias=_OAUTH_STATE_COOKIE)] = None,
) -> RedirectResponse:
    """Завершить OAuth flow: обменять code на токен, зашифровать, сохранить.

    Все ошибки (битый state, expired state, FS отклонил) редиректят на
    ``settings.fs_frontend_failure_url`` с короткой ``?error=`` меткой.
    Успех — на ``fs_frontend_success_url``. Это удобнее, чем отдавать
    JSON, потому что callback всегда открывается в браузере user'а.
    """
    storage = _require_token_storage(settings)
    client_id = _require_client_id(settings)

    if error:
        logger.info("FS OAuth declined by user: %s", error)
        return _failure_redirect(settings, reason="declined")
    if not code or not state:
        return _failure_redirect(settings, reason="missing_params")
    # CSRF: cookie обязан совпадать с query-state. Если cookie нет —
    # callback пришёл без нашего start'а (CSRF / replay).
    if state_cookie is None or not secrets.compare_digest(state_cookie, state):
        logger.warning("FS OAuth state cookie mismatch (possible CSRF)")
        return _failure_redirect(settings, reason="state_mismatch")

    redis_client = _make_redis_client(settings)
    try:
        record = await consume_state(redis_client, state)
    finally:
        await redis_client.aclose()
    if record is None:
        return _failure_redirect(settings, reason="state_expired")

    auth = FamilySearchAuth(client_id=client_id, config=_fs_config_for(settings))
    auth_request = AuthorizationRequest(
        authorize_url="",
        code_verifier=record.code_verifier,
        state=record.state,
    )
    try:
        token = await auth.complete_flow(
            code=code,
            request=auth_request,
            redirect_uri=record.redirect_uri,
        )
    except AuthError as e:
        logger.info("FS OAuth token exchange rejected: %s", e)
        return _failure_redirect(settings, reason="token_exchange_failed")
    except (ClientError, ServerError) as e:
        logger.warning("FS OAuth token endpoint upstream error: %s", e)
        return _failure_redirect(settings, reason="upstream_error")

    fs_user_id = await _fetch_fs_user_id(token.access_token, settings)
    stored = _stored_token_from_oauth(token, fs_user_id=fs_user_id)
    ciphertext = storage.encrypt(stored)

    user = (
        await session.execute(select(User).where(User.id == record.user_id))
    ).scalar_one_or_none()
    if user is None:
        # Юзер мог быть удалён между start и callback'ом — фолбэк на email.
        user = await _ensure_owner(session, settings.owner_email)
    user.fs_token_encrypted = ciphertext
    await session.flush()

    logger.info(
        "FS OAuth completed: user_id=%s fs_user_id=%s token_fp=%s",
        user.id,
        fs_user_id,
        _token_fingerprint(token.access_token),
    )

    redirect = RedirectResponse(url=settings.fs_frontend_success_url, status_code=302)
    redirect.delete_cookie(_OAUTH_STATE_COOKIE)
    return redirect


def _failure_redirect(settings: Settings, *, reason: str) -> RedirectResponse:
    """RedirectResponse на frontend-failure URL с пометкой причины.

    ``reason`` намеренно короткий и без user-data: значение попадает в
    URL и в логи браузера, поэтому не шлём ни raw-error message, ни
    state/code наружу.
    """
    sep = "&" if "?" in settings.fs_frontend_failure_url else "?"
    url = f"{settings.fs_frontend_failure_url}{sep}reason={reason}"
    response = RedirectResponse(url=url, status_code=302)
    response.delete_cookie(_OAUTH_STATE_COOKIE)
    return response


async def _fetch_fs_user_id(access_token: str, settings: Settings) -> str | None:
    """``GET /platform/users/current`` — узнать FS user id (для traceability).

    Не падает наружу: если FS не отдаёт current_user (не тот scope, sandbox),
    возвращает None. Для нашего pipeline это soft requirement, а не блокер.
    """
    config = _fs_config_for(settings)
    url = f"{config.api_base_url}/platform/users/current"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/x-fs-v1+json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        logger.info("FS users/current network error (ignored): %s", e)
        return None
    if not response.is_success:
        logger.info("FS users/current non-200: %s", response.status_code)
        return None
    payload = response.json()
    users = payload.get("users")
    if not isinstance(users, list) or not users:
        return None
    first = users[0]
    if not isinstance(first, dict):
        return None
    fs_id = first.get("personId") or first.get("id")
    return fs_id if isinstance(fs_id, str) else None


@router.delete(
    "/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить сохранённый FamilySearch-токен",
)
async def disconnect(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Затереть ``users.fs_token_encrypted``. Идемпотентно: 204 даже если
    токена не было."""
    user = await _ensure_owner(session, settings.owner_email)
    if user.fs_token_encrypted is not None:
        user.fs_token_encrypted = None
        await session.flush()
        logger.info("FS token disconnected for user_id=%s", user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=FamilySearchAccountInfo,
    summary="Подключён ли FamilySearch и до каких пор валиден токен",
)
async def familysearch_me(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FamilySearchAccountInfo:
    """Статус подключения. Не возвращает access_token — это секрет."""
    user = await _ensure_owner(session, settings.owner_email)
    if not user.fs_token_encrypted:
        return FamilySearchAccountInfo(connected=False)
    storage = _require_token_storage(settings)
    try:
        token = storage.decrypt(user.fs_token_encrypted)
    except TokenCryptoError:
        # Битый ciphertext (например, ключ ротировали без MultiFernet) —
        # с точки зрения user'а «не подключено», прозрачно ему скажем.
        logger.warning("FS token ciphertext for user_id=%s could not be decrypted", user.id)
        return FamilySearchAccountInfo(connected=False)
    return FamilySearchAccountInfo(
        connected=True,
        fs_user_id=token.fs_user_id,
        scope=token.scope,
        expires_at=token.expires_at,
        needs_refresh=token.is_expired(),
    )


# ============================================================================
# Pedigree preview + async-import (Phase 5.1).
# ============================================================================


async def _load_token_or_409(
    session: AsyncSession, settings: Settings, *, owner_email: str
) -> tuple[User, FsStoredToken]:
    """Расшифровать сохранённый токен. 409 если user не подключал FS."""
    user = await _ensure_owner(session, owner_email)
    if not user.fs_token_encrypted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="FamilySearch is not connected. Start OAuth flow first.",
        )
    storage = _require_token_storage(settings)
    try:
        token = storage.decrypt(user.fs_token_encrypted)
    except TokenCryptoError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stored FamilySearch token is invalid: {e}",
        ) from e
    return user, token


@router.get(
    "/pedigree/preview",
    response_model=FamilySearchPedigreePreviewResponse,
    summary="Показать summary pedigree до запуска импорта",
)
async def pedigree_preview(
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    fs_person_id: Annotated[str, Query(pattern=r"^[A-Z0-9-]+$", max_length=64)],
    generations: Annotated[int, Query(ge=1, le=8)] = 4,
) -> FamilySearchPedigreePreviewResponse:
    """Read-only проба pedigree: count + sample names. ImportJob не создаётся."""
    _user, token = await _load_token_or_409(session, settings, owner_email=settings.owner_email)

    config = _fs_config_for(settings)
    try:
        async with FamilySearchClient(access_token=token.access_token, config=config) as client:
            tree = await client.get_pedigree(fs_person_id, generations=generations)
    except FsNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FamilySearch person {fs_person_id} not found",
        ) from e
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"FamilySearch rejected stored token: {e}",
        ) from e
    except RateLimitError as e:
        headers = {"Retry-After": str(int(e.retry_after))} if e.retry_after else {}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="FamilySearch rate limit exceeded",
            headers=headers,
        ) from e
    except (ClientError, ServerError) as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"FamilySearch upstream error: {e}",
        ) from e

    persons = _flatten_persons(tree)
    sample = [_preview_person(p) for p in persons[:_PREVIEW_SAMPLE_LIMIT]]
    return FamilySearchPedigreePreviewResponse(
        fs_focus_person_id=fs_person_id,
        generations=generations,
        person_count=len(persons),
        sample_persons=sample,
        fs_user_id=token.fs_user_id,
    )


def _flatten_persons(tree: object) -> list[FsPerson]:
    """Обойти :class:`FsPedigreeNode` рекурсивно, собрать unique persons.

    Дублирует ``familysearch_importer._collect_persons``, но не зовёт
    его напрямую: importer — heavy module с импортом ORM, а preview
    нужен лёгкий путь без транзакции.
    """
    seen: dict[str, FsPerson] = {}

    def visit(node: object) -> None:
        # ``FsPedigreeNode`` имеет ``person``, ``father``, ``mother``.
        person = getattr(node, "person", None)
        if person is not None and person.id not in seen:
            seen[person.id] = person
        for child_attr in ("father", "mother"):
            child = getattr(node, child_attr, None)
            if child is not None:
                visit(child)

    visit(tree)
    return list(seen.values())


def _preview_person(person: FsPerson) -> FamilySearchPedigreePreviewPerson:
    """Sample-проекция персоны: name + lifespan."""
    primary_name = None
    for name in person.names:
        if name.preferred and name.full_text:
            primary_name = name.full_text
            break
    if primary_name is None and person.names:
        # Фолбэк на первое имя, даже если preferred-флага не было.
        primary_name = person.names[0].full_text or None

    birth = next((f.date_original for f in person.facts if f.type == "Birth"), None)
    death = next((f.date_original for f in person.facts if f.type == "Death"), None)
    lifespan: str | None = None
    if birth or death:
        left = f"b. {birth}" if birth else "b. ?"
        right = f"d. {death}" if death else "d. ?"
        lifespan = f"{left} – {right}"

    return FamilySearchPedigreePreviewPerson(
        fs_person_id=person.id,
        primary_name=primary_name,
        lifespan=lifespan,
    )


@router.post(
    "/import",
    response_model=ImportJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить async-импорт FS pedigree (использует server-side токен)",
)
async def create_async_import(
    request: FamilySearchAsyncImportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_session)],
    pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    # Phase 12.0: FS-импорт доступен только на Pro-плане.
    _entitlement: Annotated[None, require_feature("fs_import_enabled")] = None,
) -> ImportJobResponse:
    """Создать ``ImportJob`` (queued) и enqueue ``run_fs_import_job`` в arq.

    UI получает 202 + ``events_url`` и подписывается на SSE. Сам импорт
    делает worker — см. ``parser_service.worker.run_fs_import_job``.

    Если у user'а нет сохранённого токена — 409 (фронт редиректит на
    ``/familysearch/connect``).
    Если токен протух и refresh-токен отсутствует — тоже 409 с тем же
    кодом, чтобы фронт видел один путь восстановления.
    """
    user, _token = await _load_token_or_409(session, settings, owner_email=settings.owner_email)

    tree = (
        await session.execute(select(Tree).where(Tree.id == request.tree_id))
    ).scalar_one_or_none()
    if tree is None:
        # Фолбэк: автоматически создаём дерево с именем по fs_person_id —
        # удобно, когда фронт сразу делает «Connect → Import» без шага
        # выбора tree. Для тестов проще: они создают tree вручную и
        # передают tree_id, который существует.
        # Решение быть permissive здесь — компромисс UX vs. строгости;
        # если treeId не существует, считаем это ошибкой клиента (404).
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tree {request.tree_id} not found",
        )

    job = ImportJob(
        tree_id=tree.id,
        created_by_user_id=user.id,
        source_kind=ImportSourceKind.FAMILYSEARCH.value,
        source_filename=None,
        source_size_bytes=None,
        status=ImportJobStatus.QUEUED.value,
        stats={},
        errors=[],
        progress=None,
        cancel_requested=False,
    )
    session.add(job)
    await session.flush()

    await pool.enqueue_job(
        RUN_FS_IMPORT_JOB_NAME,
        str(job.id),
        str(user.id),
        request.fs_person_id,
        request.generations,
        _queue_name=settings.arq_queue_name,
    )

    response = ImportJobResponse.model_validate(job)
    return response.model_copy(update={"events_url": _events_url(job.id)})


# ============================================================================
# Sugar polling endpoint (Phase 5.0).
# ============================================================================


@router.get(
    "/{job_id}",
    response_model=ImportJobResponse,
    summary="Получить статус FS-импорта",
)
async def get_familysearch_import(
    job_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ImportJobResponse:
    """Повторно загрузить ``ImportJob`` (для polling-сценариев фронта).

    Не отличается семантически от ``GET /imports/{id}`` — sugar-endpoint
    для удобства, чтобы фронт мог гонять ``/imports/familysearch/{id}``
    в той же области URL, что и POST.
    """
    job = (
        await session.execute(select(ImportJob).where(ImportJob.id == job_id))
    ).scalar_one_or_none()
    if job is None or job.source_kind != ImportSourceKind.FAMILYSEARCH.value:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"FamilySearch import job {job_id} not found",
        )
    return ImportJobResponse.model_validate(job)
