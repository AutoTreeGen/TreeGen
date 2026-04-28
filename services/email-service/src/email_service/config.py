"""email-service settings (pydantic-settings).

ENV-префикс ``EMAIL_SERVICE_``. См. README §«ENV».
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация email-service."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )

    # ---- Resend ----
    resend_api_key: str = Field(
        default="",
        description="re_* — API-ключ Resend.",
    )
    resend_from: str = Field(
        default="noreply@smartreedna.com",
        description="From-адрес. Должен быть verified domain в Resend.",
    )

    # ---- Brand ----
    brand_name: str = Field(default="SmarTreeDNA")
    support_email: str = Field(default="support@smartreedna.com")
    web_base_url: str = Field(
        default="http://localhost:3000",
        description="База для CTA-ссылок в email'ах (settings, /share, /pricing).",
    )

    # ---- Feature flag ----
    enabled: bool = Field(
        default=True,
        description=(
            "Если False — все /send запросы помечаются skipped_optout без "
            "вызова Resend. Удобно для local dev и CI без RESEND_API_KEY."
        ),
    )

    # ---- HTTP ----
    resend_timeout_seconds: float = Field(default=10.0, ge=1.0)

    debug: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_prefix="EMAIL_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ; для тестов: ``get_settings.cache_clear()``."""
    return Settings()
