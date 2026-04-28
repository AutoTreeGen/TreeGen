"""Clerk JWT verification (Phase 4.10, ADR-0033).

Проверяет Bearer-токен, выпущенный Clerk:

* RS256-подпись против ключа из ``{issuer}/.well-known/jwks.json``.
* ``iss`` сравнивается с настроенным issuer (CLERK_ISSUER ENV).
* ``exp`` проверяется на свежесть (PyJWT делает leeway-aware).
* ``aud`` проверяется при наличии ``audience`` в настройках; иначе
  игнорируется (Clerk frontend tokens не содержат aud по умолчанию).

JWKS-ключи кэшируются in-memory с TTL по умолчанию 600s; rotation в
Clerk (которая перевыпускает kid) подхватывается на следующий refresh.

Декомпозиция модуля:

* :class:`ClerkJwtSettings` — иммутабельный конфиг (issuer/audience/...).
* :class:`JwksCache` — async-кэш JWKS-документа.
* :func:`verify_clerk_jwt` — высокоуровневая проверка одного токена.

Пакет shared-models целенаправленно содержит этот код, чтобы три сервиса
(parser-service, dna-service, notification-service) не клонировали
одну и ту же логику. ADR-0033 §«Decision» — единая верификация.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient, PyJWKClientError, PyJWTError

logger = logging.getLogger(__name__)


# RS256 — единственный алгоритм, который Clerk использует для frontend
# tokens; whitelist жёсткий, чтобы исключить downgrade-attack на HS256
# с известным секретом.
_ALLOWED_ALGORITHMS: tuple[str, ...] = ("RS256",)

# Default JWKS TTL — компромисс между ротацией ключей в Clerk и
# отсутствием round-trip'а на каждом запросе. Clerk сам отдаёт
# ``Cache-Control: max-age=3600`` на /.well-known/jwks.json, но не у всех
# инфраструктур есть HTTP cache; держим явный TTL.
_DEFAULT_JWKS_TTL_SECONDS: float = 600.0

# Сетевой таймаут для JWKS GET'а — не должен подвешивать запрос.
_JWKS_FETCH_TIMEOUT_SECONDS: float = 5.0


class AuthError(Exception):
    """Единая ошибка для всех путей верификации.

    FastAPI dependency-обёртка ловит её и превращает в HTTP 401. Текст
    сообщения наружу не отдаём (см. ``dependencies.get_current_claims``);
    логируем здесь для traceability.
    """


@dataclass(frozen=True, slots=True)
class ClerkJwtSettings:
    """Конфиг верификации JWT.

    Атрибуты:
        issuer: ``iss``-claim, который Clerk пишет в токены. Для дев-
            окружения — ``https://accept-XXXX.clerk.accounts.dev``;
            для прод — кастомный домен или ``https://clerk.{your-app}.com``.
        jwks_url: URL JWKS-документа. Если None, берётся
            ``{issuer.rstrip('/')}/.well-known/jwks.json``.
        audience: optional ``aud``-claim для проверки. None — пропускаем.
        leeway_seconds: допуск на clock skew при проверке ``exp``/``nbf``.
            Clerk советует 0–60s; берём 30 как разумный default.
        jwks_ttl_seconds: TTL JWKS-кэша.
    """

    issuer: str
    jwks_url: str | None = None
    audience: str | None = None
    leeway_seconds: int = 30
    jwks_ttl_seconds: float = _DEFAULT_JWKS_TTL_SECONDS

    def resolved_jwks_url(self) -> str:
        """Вернуть JWKS URL, выводя его из issuer если не задан явно."""
        if self.jwks_url:
            return self.jwks_url
        return f"{self.issuer.rstrip('/')}/.well-known/jwks.json"


@dataclass(frozen=True, slots=True)
class ClerkClaims:
    """Подмножество claim'ов Clerk JWT, нужных приложению.

    Полные claims хранятся в :attr:`raw` для редких случаев, когда нужна
    кастомная мета (organization, session id и т.д.). Большинству
    endpoint'ов достаточно ``sub`` + ``email``.
    """

    sub: str
    email: str | None
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


class JwksCache:
    """Тонкая обёртка над :class:`jwt.PyJWKClient` с asyncio-locking.

    PyJWKClient синхронный (использует ``urllib`` под капотом), но
    обёртываем его в ``run_in_executor``, чтобы не блокировать event
    loop. Сам `PyJWKClient` имеет встроенный TTL-кэш; здесь мы добавляем
    instance-уровень locking — чтобы одновременные запросы не плодили
    конкурентные fetch'ы при первом обращении.

    На прод-нагрузке этот класс приватный к
    :func:`verify_clerk_jwt`; в тестах удобно подменять напрямую.
    """

    def __init__(self, jwks_url: str, ttl_seconds: float = _DEFAULT_JWKS_TTL_SECONDS) -> None:
        self._jwks_url = jwks_url
        self._client = PyJWKClient(jwks_url, lifespan=int(ttl_seconds))
        self._lock = asyncio.Lock()
        # Mark последнего refresh'а — для тестов / debug.
        self._last_refresh_at: float = 0.0

    async def get_signing_key(self, token: str) -> Any:
        """Вернуть signing key для данного токена (по kid в его header'е).

        :raises AuthError: если ключ не найден или JWKS unreachable.
        """
        loop = asyncio.get_running_loop()
        async with self._lock:
            try:
                # Run sync resolver в default ThreadPoolExecutor.
                signing_key = await loop.run_in_executor(
                    None, self._client.get_signing_key_from_jwt, token
                )
            except PyJWKClientError as exc:
                logger.warning("JWKS lookup failed for url=%s: %s", self._jwks_url, exc)
                msg = "Failed to resolve signing key from Clerk JWKS"
                raise AuthError(msg) from exc
            except (httpx.HTTPError, OSError) as exc:
                # urllib бросает OSError, httpx — HTTPError; оба
                # описывают "не могу сходить за JWKS".
                logger.warning("JWKS fetch error for url=%s: %s", self._jwks_url, exc)
                msg = "Cannot reach Clerk JWKS endpoint"
                raise AuthError(msg) from exc
            self._last_refresh_at = time.time()
            return signing_key


# Module-level кэш JwksCache по URL. Хочется один экземпляр на весь
# процесс, иначе на каждом запросе новый PyJWKClient → новый JWKS round-trip.
_JWKS_CACHE_BY_URL: dict[str, JwksCache] = {}
_JWKS_CACHE_LOCK = asyncio.Lock()


async def _get_or_create_jwks_cache(settings: ClerkJwtSettings) -> JwksCache:
    """Singleton-фабрика :class:`JwksCache` per-jwks_url.

    Lock'ится на module-level ``asyncio.Lock``, чтобы dедуплицировать
    создание JwksCache при гонках на холодном старте (несколько
    одновременных запросов до первого ответа).
    """
    url = settings.resolved_jwks_url()
    async with _JWKS_CACHE_LOCK:
        cache = _JWKS_CACHE_BY_URL.get(url)
        if cache is None:
            cache = JwksCache(url, ttl_seconds=settings.jwks_ttl_seconds)
            _JWKS_CACHE_BY_URL[url] = cache
        return cache


def reset_jwks_cache() -> None:
    """Очистить module-level JWKS-кэш (для тестов между сценариями)."""
    _JWKS_CACHE_BY_URL.clear()


async def verify_clerk_jwt(
    token: str,
    settings: ClerkJwtSettings,
    *,
    jwks_cache: JwksCache | None = None,
) -> ClerkClaims:
    """Проверить Bearer JWT и вернуть :class:`ClerkClaims`.

    Args:
        token: raw JWT (без ``Bearer `` префикса).
        settings: конфиг верификации.
        jwks_cache: optional override для тестов; иначе берётся
            module-level singleton.

    Returns:
        :class:`ClerkClaims` с ``sub``, ``email`` и raw claims.

    Raises:
        AuthError: если signature, exp, iss или JWKS не пропускают
            токен. Текст ошибки наружу не уходит (см. dependencies.py).
    """
    if not token:
        msg = "Empty Bearer token"
        raise AuthError(msg)

    cache = jwks_cache or await _get_or_create_jwks_cache(settings)
    signing_key = await cache.get_signing_key(token)

    decode_options: dict[str, Any] = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
        "require": ["sub", "iss", "exp"],
    }
    if settings.audience is None:
        # PyJWT пропускает aud-проверку, если её ключ выключен.
        decode_options["verify_aud"] = False

    try:
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(_ALLOWED_ALGORITHMS),
            issuer=settings.issuer,
            audience=settings.audience,
            leeway=settings.leeway_seconds,
            options=decode_options,
        )
    except PyJWTError as exc:
        # Все суб-классы (ExpiredSignatureError, InvalidIssuerError,
        # InvalidSignatureError, ...) — все сводятся к 401. Текст в логе.
        logger.info("Clerk JWT decode failed: %s", exc)
        msg = "Clerk JWT verification failed"
        raise AuthError(msg) from exc

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        msg = "Clerk JWT missing sub claim"
        raise AuthError(msg)

    # Clerk frontend-tokens по дефолту не несут ``email``; лежит в
    # ``email_addresses[0]`` через webhook. Для verification это OK:
    # email подтянем JIT через Clerk API, либо оставим None и
    # потребуем webhook-flow для user-row creation.
    raw_email = claims.get("email")
    email: str | None = raw_email if isinstance(raw_email, str) and raw_email else None
    return ClerkClaims(sub=sub, email=email, raw=dict(claims))


__all__ = [
    "AuthError",
    "ClerkClaims",
    "ClerkJwtSettings",
    "JwksCache",
    "reset_jwks_cache",
    "verify_clerk_jwt",
]
