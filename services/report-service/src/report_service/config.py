"""report-service settings (pydantic-settings).

ENV-префикс ``REPORT_SERVICE_``. Кешируем экземпляр через ``lru_cache``
чтобы не парсить env на каждый Depends.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация report-service."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )

    debug: bool = Field(default=False)

    # Signed URL TTL для сгенерированных PDF (секунды). 24h — достаточно,
    # чтобы пользователь успел переслать клиенту.
    pdf_url_ttl_seconds: int = Field(default=24 * 3600, ge=60)

    model_config = SettingsConfigDict(
        env_prefix="REPORT_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ к настройкам.

    Кешируем на уровень процесса; для тестов сбрасывать через
    ``get_settings.cache_clear()`` после ``monkeypatch.setenv``.
    """
    return Settings()
