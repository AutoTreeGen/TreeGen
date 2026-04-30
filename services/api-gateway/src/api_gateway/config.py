"""api-gateway settings (pydantic-settings).

ENV-префикс ``API_GATEWAY_``. См. README §«ENV».
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация api-gateway."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )

    # ---- Clerk authentication (Phase 4.10 / ADR-0033) ---------------------
    clerk_issuer: str = Field(
        default="",
        description=(
            "Clerk issuer URL. Если пусто — auth-зависимости вернут 503 и endpoint'ы недоступны."
        ),
    )
    clerk_jwks_url: str = Field(
        default="",
        description="Override JWKS URL. Пусто — берётся ``{issuer}/.well-known/jwks.json``.",
    )
    clerk_audience: str = Field(
        default="",
        description="Optional ``aud``-claim. Пусто — пропускаем aud-проверку.",
    )

    debug: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_prefix="API_GATEWAY_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ; для тестов: ``get_settings.cache_clear()``."""
    return Settings()
