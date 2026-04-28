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
