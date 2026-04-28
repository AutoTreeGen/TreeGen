"""Конфигурация parser-service через pydantic-settings.

Источники: переменные окружения, .env (через python-dotenv).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки parser-service.

    Все поля переопределяются через ENV: ``DATABASE_URL``, ``PARSER_SERVICE_DEBUG``
    и т.п. Префикс ``PARSER_SERVICE_`` опускается для общеизвестных переменных.
    """

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres (asyncpg или psycopg).",
    )
    debug: bool = Field(default=False, description="FastAPI debug-режим.")
    owner_email: str = Field(
        default="owner@autotreegen.local",
        description="Email пользователя-владельца дерева для импорта.",
    )
    max_upload_mb: int = Field(
        default=200,
        description="Лимит размера GEDCOM-upload в мегабайтах.",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description=(
            "Redis URL для arq-очереди и pubsub-канала ``job-events:{job_id}``. "
            "Phase 3.5: один Redis обслуживает обе цели."
        ),
    )
    arq_queue_name: str = Field(
        default="imports",
        description="Имя arq-очереди для async-импортов (синхронизировано с воркером).",
    )

    model_config = SettingsConfigDict(
        env_prefix="PARSER_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def get_settings() -> Settings:
    """Получить инстанс настроек.

    Вызывается через FastAPI Depends — pydantic-settings кеширует чтение env.
    """
    return Settings()
