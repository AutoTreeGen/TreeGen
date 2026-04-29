"""Phase 13.2 — production security middleware для всех FastAPI-сервисов.

Единственная точка входа — :func:`apply_security_middleware` (см. ADR-0053):
вешает CORS, request-size limit, security headers и slowapi rate-limiter за
один вызов.

Использование (типичный startup hook сервиса)::

    from shared_models.security import apply_security_middleware

    app = FastAPI(...)
    apply_security_middleware(app, service_name="parser-service")

Per-route более строгий лимит — через ``request.app.state.limiter``::

    from fastapi import Request
    from slowapi import Limiter  # type: ignore[import-untyped]

    @app.post("/auth/login")
    async def login(request: Request) -> dict:
        # ``app.state.limiter`` установлен в apply_security_middleware
        limiter: Limiter = request.app.state.limiter
        await limiter.limit("10/minute")(request)
        ...

См. ADR-0053 для обоснования slowapi/in-memory, CSP-стратегии и заголовков.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, Final

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Default тариф — 100 запросов в минуту с одного IP. Для строгих ручек
# (auth/webhook) сервис применяет ``@limiter.limit("10/minute")`` на route.
_DEFAULT_RATE_LIMIT: Final = "100/minute"

# 1 МБ — запас по сравнению с типичным JSON-payload'ом, но reject для
# случайных гигабайт. ``/imports/*`` — отдельный путь (GEDCOM-файлы).
_DEFAULT_MAX_BODY_BYTES: Final = 1_000_000
_LARGE_BODY_PATH_PREFIXES: Final[tuple[str, ...]] = ("/imports",)
_LARGE_MAX_BODY_BYTES: Final = 200_000_000

# Security headers — добавляются к каждому HTTP-ответу.
_SECURITY_HEADERS: Final[dict[bytes, bytes]] = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    # Permissions-Policy — отключаем по умолчанию device-features, которые
    # сервис заведомо не использует. См. ADR-0053 §«Permissions-Policy».
    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
}
# HSTS добавляется только для запросов с scheme=https (local dev = http).
_HSTS_HEADER: Final = b"strict-transport-security"
_HSTS_VALUE: Final = b"max-age=31536000; includeSubDomains; preload"


def _parse_origins(env_value: str | None) -> list[str]:
    """Распарсить ``CORS_ORIGINS`` (comma-separated). Default — local dev."""
    if not env_value:
        return ["http://localhost:3000"]
    return [origin.strip() for origin in env_value.split(",") if origin.strip()]


class SecurityHeadersMiddleware:
    """ASGI-middleware: добавляет security headers ко всем HTTP-ответам.

    HSTS добавляется только для https-scheme — иначе ломаем локальный
    `uv run uvicorn ... --reload` (он по http).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        is_https = scope.get("scheme") == "https"

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Headers — list of (bytes, bytes); MutableMapping для безопасной модификации.
                raw_headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                existing_keys = {key.lower() for key, _ in raw_headers}
                for header_key, header_value in _SECURITY_HEADERS.items():
                    if header_key not in existing_keys:
                        raw_headers.append((header_key, header_value))
                if is_https and _HSTS_HEADER not in existing_keys:
                    raw_headers.append((_HSTS_HEADER, _HSTS_VALUE))
                message["headers"] = raw_headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


class MaxBodySizeMiddleware:
    """ASGI-middleware: 413 Payload Too Large если ``Content-Length`` > limit.

    Не читает body заранее — проверяет только заголовок. Если клиент отправил
    chunked без Content-Length, это middleware пропустит запрос (защиту от
    bombing на этом случае дают request-deadline + uvicorn ``--limit-max-requests``).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_size: int = _DEFAULT_MAX_BODY_BYTES,
        large_max_size: int = _LARGE_MAX_BODY_BYTES,
        large_paths: tuple[str, ...] = _LARGE_BODY_PATH_PREFIXES,
    ) -> None:
        self.app = app
        self.max_size = max_size
        self.large_max_size = large_max_size
        self.large_paths = large_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_large_path = any(path.startswith(prefix) for prefix in self.large_paths)
        limit = self.large_max_size if is_large_path else self.max_size

        for header_key, header_value in scope.get("headers", []):
            if header_key.lower() == b"content-length":
                try:
                    content_length = int(header_value)
                except ValueError:
                    break
                if content_length > limit:
                    response = JSONResponse(
                        {"detail": f"Request body too large; max {limit} bytes."},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
                break

        await self.app(scope, receive, send)


def apply_security_middleware(
    app: FastAPI,
    *,
    service_name: str,
    default_rate_limit: str = _DEFAULT_RATE_LIMIT,
    cors_origins_env_var: str = "CORS_ORIGINS",
) -> None:
    """Подключить security middleware к ``app`` за один вызов.

    Порядок применения (Starlette: первый добавленный = outermost):

    1. ``CORSMiddleware`` — outermost; обрабатывает preflight ``OPTIONS``.
    2. ``MaxBodySizeMiddleware`` — отсекаем 413 до auth.
    3. ``SlowAPIMiddleware`` — rate limit (``app.state.limiter``).
    4. ``SecurityHeadersMiddleware`` — innermost; декорирует ответы.

    Также устанавливает ``app.state.limiter`` (slowapi.Limiter) и
    ``app.state.service_name`` для удобства конкретных ручек:

    .. code-block:: python

        @app.post("/auth/refresh")
        async def refresh(request: Request) -> ...:
            await request.app.state.limiter.limit("10/minute")(request)

    Args:
        app: FastAPI instance.
        service_name: Логическое имя (для тегов в логах rate-limit).
        default_rate_limit: slowapi-формат, например ``"100/minute"``.
        cors_origins_env_var: ENV var с allowed origins (comma-separated).
    """
    # slowapi импортируется здесь, чтобы пакет shared-models оставался
    # импортируемым без extras. ``shared-models[security]`` ставит slowapi.
    from slowapi import (  # noqa: PLC0415
        Limiter,
        _rate_limit_exceeded_handler,
    )
    from slowapi.errors import RateLimitExceeded  # noqa: PLC0415
    from slowapi.middleware import (  # noqa: PLC0415
        SlowAPIMiddleware,
    )
    from slowapi.util import get_remote_address  # noqa: PLC0415

    # ``RATE_LIMITING_ENABLED=false`` отключает limiter полностью — нужно для
    # тестов, где shared in-memory storage отравляет state между тест-файлами
    # одного процесса. См. tests/conftest.py каждого сервиса.
    rate_limiting_enabled = (
        os.environ.get("RATE_LIMITING_ENABLED", "true").strip().lower() != "false"
    )
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[default_rate_limit],
        # Storage — in-memory per-process. Cloud Run-инстансы не делят
        # счётчик, эффективный лимит = N_instances × limit. См. ADR-0053
        # §«In-memory trade-off».
        storage_uri="memory://",
        enabled=rate_limiting_enabled,
    )
    app.state.limiter = limiter
    app.state.service_name = service_name
    # _rate_limit_exceeded_handler возвращает 429 + Retry-After.
    _register_rate_limit_handler(app, _rate_limit_exceeded_handler, RateLimitExceeded)

    # Порядок add_middleware: первый добавленный — outermost (request видит первым).
    app.add_middleware(CORSMiddleware, **_cors_kwargs(cors_origins_env_var))
    app.add_middleware(MaxBodySizeMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)


def _cors_kwargs(env_var: str) -> MutableMapping[str, Any]:
    """CORS-параметры — origins из env, остальное стандарт."""
    origins = _parse_origins(os.environ.get(env_var))
    return {
        "allow_origins": origins,
        # Bearer-токены идут через Authorization header — credentials=True
        # позволяет фронту передать его при cross-origin запросах.
        "allow_credentials": True,
        "allow_methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        "allow_headers": ["*"],
    }


def _register_rate_limit_handler(
    app: FastAPI,
    handler: Callable[..., Awaitable[Any]],
    exc_class: type[Exception],
) -> None:
    """Регистрируем handler под exception-классом slowapi.

    Выделено в отдельную функцию, чтобы скрыть слабо-типизированный slowapi
    интерфейс от строгих mypy-проверок в основной функции.
    """
    app.add_exception_handler(exc_class, handler)
