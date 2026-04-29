"""notification-service settings (pydantic-settings).

ENV-префикс ``NOTIFICATION_SERVICE_`` — например
``NOTIFICATION_SERVICE_DATABASE_URL``. См. ADR-0024.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация notification-service."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )
    idempotency_window_minutes: int = Field(
        default=60,
        ge=1,
        description=(
            "Окно идемпотентности — повторная отправка той же тройки "
            "(user_id, event_type, idempotency_key) в этом окне возвращает "
            "существующий notification_id вместо повторного INSERT. "
            "См. ADR-0024."
        ),
    )
    debug: bool = Field(default=False)

    # ---- Telegram channel (Phase 14.1, ADR-0056) ----------------------------
    telegram_bot_url: str = Field(
        default="",
        description=(
            "Base URL telegram-bot service (e.g. ``http://telegram-bot:8006``). "
            "Пусто → TelegramChannel.send() возвращает False с reason='not_configured', "
            "что позволяет dispatcher'у graceful-skip без exception'а."
        ),
    )
    telegram_internal_token: str = Field(
        default="",
        description=(
            "Shared secret для X-Internal-Service-Token header'а на /telegram/notify. "
            "Должен совпадать с TELEGRAM_BOT_INTERNAL_SERVICE_TOKEN. Пусто → skip как "
            "telegram_bot_url."
        ),
    )
    telegram_request_timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="HTTP timeout для bot-вызова (sync push в hot path notify-flow'а).",
    )

    # ---- Clerk authentication (Phase 4.10, ADR-0033) ------------------------
    clerk_issuer: str = Field(
        default="",
        description=(
            "Clerk issuer URL. Пусто — auth-зависимости вернут 503. Тесты подменяют через фикстуры."
        ),
    )
    clerk_jwks_url: str = Field(
        default="",
        description="Override JWKS URL; пусто — выводится из issuer'а.",
    )
    clerk_audience: str = Field(
        default="",
        description="Optional ``aud``; пусто — пропускаем aud-проверку.",
    )

    model_config = SettingsConfigDict(
        env_prefix="NOTIFICATION_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def get_settings() -> Settings:
    """Получить инстанс настроек (pydantic-settings кеширует чтение env)."""
    return Settings()
