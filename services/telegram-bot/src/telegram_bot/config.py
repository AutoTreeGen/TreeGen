"""telegram-bot settings (pydantic-settings).

ENV-префикс ``TELEGRAM_BOT_``. См. README §«ENV».
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация telegram-bot."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL для one-time link-токенов.",
    )

    # ---- Telegram Bot API ----
    bot_token: str = Field(
        default="",
        description="Bot-токен от @BotFather. Пустой → outbound отключён.",
    )
    webhook_secret: str = Field(
        default="",
        description=(
            "Секрет для X-Telegram-Bot-Api-Secret-Token валидации. "
            "Пустой → webhook отвечает 503 (отказ обслуживания), "
            "потому что без секрета любой может слать update'ы."
        ),
    )
    bot_api_base_url: str = Field(
        default="https://api.telegram.org",
        description="Override для тестов (httpx.MockTransport не нужен URL).",
    )

    # ---- Linking ----
    link_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description="TTL one-time link-токена в Redis (15 min default).",
    )
    web_base_url: str = Field(
        default="http://localhost:3000",
        description="База web-у для /telegram/link?token=... ссылок.",
    )

    # ---- HTTP ----
    bot_api_timeout_seconds: float = Field(default=10.0, ge=1.0)

    # ---- Internal service auth (Phase 14.1, ADR-0056) ----
    # Shared secret для notification-service → telegram-bot вызовов.
    # Notification-service кладёт значение в ``X-Internal-Service-Token``
    # header; bot сравнивает constant-time. Пустой → /notify endpoint
    # возвращает 503 (отказ обслуживания), потому что без секрета любой
    # может слать push'и нашим user'ам.
    internal_service_token: str = Field(
        default="",
        description=(
            "Shared secret для X-Internal-Service-Token validation "
            "на /telegram/notify endpoint. Пустой → 503."
        ),
    )

    # ---- Phase 14.2 — weekly digest worker ----
    # Token для исходящих вызовов parser-service /users/{id}/digest-summary
    # (зеркало `internal_service_token`, который parser-service ожидает в
    # `X-Internal-Service-Token`). На dev-машине одна и та же строка для
    # обеих сторон; в проде — Secret Manager.
    parser_service_base_url: str = Field(
        default="http://localhost:8000",
        description=(
            "Base URL parser-service для weekly-digest worker. Без trailing "
            "slash. В проде — internal cluster-DNS, не публичный домен."
        ),
    )
    parser_service_internal_token: str = Field(
        default="",
        description=(
            "Shared secret отправляемый в X-Internal-Service-Token при вызове "
            "parser-service /users/{id}/digest-summary. Пустой → digest worker "
            "skip'нет цикл с warning'ом (fail-safe)."
        ),
    )

    debug: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_prefix="TELEGRAM_BOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ; для тестов: ``get_settings.cache_clear()``."""
    return Settings()
