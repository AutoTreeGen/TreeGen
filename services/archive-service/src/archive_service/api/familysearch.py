"""FastAPI router для FamilySearch read-only proxy (Phase 9.0 / ADR-0055).

Endpoints (все требуют Clerk Bearer JWT):

* ``GET /archives/familysearch/oauth/start`` — начать PKCE flow.
* ``GET /archives/familysearch/oauth/callback`` — обменять code на token.
* ``GET /archives/familysearch/search`` — read-only proxy в FS Records.
* ``GET /archives/familysearch/person/{fsid}`` — read-only proxy в FS Tree.

Если ``FAMILYSEARCH_CLIENT_ID`` или ``FAMILYSEARCH_REDIRECT_URI`` не заданы —
ручки отдают 503. Если ``ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY`` пуст —
``oauth/callback`` отдаёт 503 (мы отказываемся хранить refresh-токены
в plaintext'е).
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as redis_asyncio
from familysearch_client import (
    AuthError,
    NotFoundError,
    RateLimitError,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status

from archive_service.adapters.familysearch import (
    AdapterRateLimitError,
    FamilySearchAdapter,
    PersonDetail,
    RecordHit,
    quota_configured,
)
from archive_service.auth import get_current_user_id
from archive_service.config import Settings, get_settings
from archive_service.redis_client import make_redis_client
from archive_service.token_storage import TokenCryptoError, TokenStorage

router = APIRouter(prefix="/archives/familysearch", tags=["familysearch"])


def require_fs_configured(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Settings:
    """Depends-страж: 503 если FAMILYSEARCH_CLIENT_ID/REDIRECT_URI пусты.

    Декларируется первым в сигнатурах ручек, чтобы 503 срабатывал
    раньше других зависимостей (например ``get_token_storage``,
    которая 503-ит на отсутствие encryption key).
    """
    if not quota_configured(settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "FamilySearch adapter not configured: "
                "FAMILYSEARCH_CLIENT_ID and/or FAMILYSEARCH_REDIRECT_URI is empty."
            ),
        )
    return settings


async def get_redis(
    settings: Annotated[Settings, Depends(get_settings)],
) -> redis_asyncio.Redis:
    """DI-фабрика Redis. Тесты подменяют через
    :func:`archive_service.redis_client._redis_client_factory`.
    """
    return make_redis_client(settings)


def get_adapter(
    settings: Annotated[Settings, Depends(get_settings)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis)],
) -> FamilySearchAdapter:
    """DI-фабрика адаптера. В тестах подменяется через ``app.dependency_overrides``."""
    return FamilySearchAdapter(settings=settings, redis=redis)


def get_token_storage(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenStorage:
    """DI-фабрика хранилища токенов; 503 при отсутствии ключа."""
    if not settings.token_encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Token storage not configured: "
                "ARCHIVE_SERVICE_TOKEN_ENCRYPTION_KEY is empty. "
                "Refusing to persist refresh tokens in plaintext."
            ),
        )
    return TokenStorage(fernet_key=settings.token_encryption_key)


# ----------------------------------------------------------------------
# OAuth
# ----------------------------------------------------------------------


@router.get("/oauth/start", summary="Начать FS PKCE-flow.")
async def oauth_start(
    settings: Annotated[Settings, Depends(require_fs_configured)],
    adapter: Annotated[FamilySearchAdapter, Depends(get_adapter)],
    _user_id: Annotated[str, Depends(get_current_user_id)],
    scope: Annotated[str | None, Query(description="OAuth scope (опционально).")] = None,
) -> dict[str, str]:
    """Возвращает ``authorize_url`` + ``state`` + ``code_verifier``.

    Frontend редиректит юзера на ``authorize_url``; FS вернётся на наш
    ``oauth/callback?code=&state=``. ``code_verifier`` мы дополнительно
    сохранили в Redis под ключом state (TTL — 10 минут default), но
    отдаём наружу — это не секрет PKCE-протокола (нужен только при
    отправке code на token endpoint).
    """
    request = adapter.start_authorize(
        redirect_uri=settings.familysearch_redirect_uri,
        scope=scope,
    )
    await adapter.save_oauth_state(request)
    return {
        "authorize_url": request.authorize_url,
        "state": request.state,
        "code_verifier": request.code_verifier,
    }


@router.get("/oauth/callback", summary="OAuth callback — обменять code на токен.")
async def oauth_callback(
    settings: Annotated[Settings, Depends(require_fs_configured)],
    adapter: Annotated[FamilySearchAdapter, Depends(get_adapter)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis)],
    storage: Annotated[TokenStorage, Depends(get_token_storage)],
    user_id: Annotated[str, Depends(get_current_user_id)],
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
) -> dict[str, str | bool]:
    """Принимает ``code`` + ``state``; шифрует токен и кладёт в Redis.

    На входе обязательно отдан тот же Clerk-юзер, что начинал flow —
    state-проверка не привязывает state к user_id (для простоты scaffold-а;
    в проде стоит добавить, см. ADR-0055 §«Open questions»).
    """
    code_verifier = await adapter.consume_oauth_state(state)
    if code_verifier is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth state expired or unknown; restart flow.",
        )
    try:
        token = await adapter.exchange_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=settings.familysearch_redirect_uri,
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    try:
        await storage.save(redis, user_id=user_id, token=token)
    except TokenCryptoError as exc:
        # Sanity: ключ был валиден на старте (TokenStorage проверил),
        # значит криптография сломалась на самом encrypt — это server-error.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token encryption failed: {exc}",
        ) from exc
    return {"connected": True, "scope": token.scope or ""}


# ----------------------------------------------------------------------
# Search / Person — read-only proxy
# ----------------------------------------------------------------------


async def _load_access_token(
    storage: TokenStorage,
    redis: redis_asyncio.Redis,
    user_id: str,
) -> str:
    """Получить access_token из шифрованного хранилища; 401 если нет."""
    stored = await storage.load(redis, user_id=user_id)
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No FamilySearch token; complete /oauth/start flow first.",
        )
    return stored.access_token


@router.get("/search", summary="FS Records Search proxy.", response_model=None)
async def get_search(
    _settings: Annotated[Settings, Depends(require_fs_configured)],
    adapter: Annotated[FamilySearchAdapter, Depends(get_adapter)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis)],
    storage: Annotated[TokenStorage, Depends(get_token_storage)],
    user_id: Annotated[str, Depends(get_current_user_id)],
    q: Annotated[str | None, Query(description="Свободный запрос FS.")] = None,
    surname: Annotated[str | None, Query()] = None,
    given: Annotated[str | None, Query()] = None,
    year: Annotated[int | None, Query()] = None,
    year_range: Annotated[int, Query(ge=0, le=50)] = 5,
) -> dict[str, list[RecordHit]]:
    access_token = await _load_access_token(storage, redis, user_id)
    try:
        hits = await adapter.search_records(
            access_token=access_token,
            user_id=user_id,
            query=q,
            surname=surname,
            given=given,
            year=year,
            year_range=year_range,
        )
    except AdapterRateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=headers,
        ) from exc
    except RateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=headers,
        ) from exc
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return {"hits": hits}


@router.get("/person/{fsid}", summary="FS Tree person proxy.", response_model=None)
async def get_person(
    _settings: Annotated[Settings, Depends(require_fs_configured)],
    adapter: Annotated[FamilySearchAdapter, Depends(get_adapter)],
    redis: Annotated[redis_asyncio.Redis, Depends(get_redis)],
    storage: Annotated[TokenStorage, Depends(get_token_storage)],
    user_id: Annotated[str, Depends(get_current_user_id)],
    fsid: str,
) -> PersonDetail:
    access_token = await _load_access_token(storage, redis, user_id)
    try:
        return await adapter.get_person(
            access_token=access_token,
            user_id=user_id,
            fsid=fsid,
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except AdapterRateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=headers,
        ) from exc
    except RateLimitError as exc:
        headers = {"Retry-After": str(int(exc.retry_after))} if exc.retry_after else {}
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers=headers,
        ) from exc
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
