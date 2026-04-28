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

    # ---- FamilySearch OAuth (Phase 5.1, ADR-0027) ---------------------------
    fs_client_id: str = Field(
        default="",
        description=(
            "FamilySearch app key (developer.familysearch.org). Пустая строка — "
            "OAuth-эндпоинты возвращают 503 (для тестов / dev-окружения без "
            "ключа)."
        ),
    )
    fs_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/imports/familysearch/oauth/callback",
        description=(
            "Зарегистрированный в FamilySearch redirect URI. "
            "Должен совпадать (схема+хост+путь) с тем, что выдан в developer console."
        ),
    )
    fs_oauth_scope: str | None = Field(
        default=None,
        description="OAuth scope (через пробел). None = FamilySearch-дефолт.",
    )
    fs_environment: str = Field(
        default="sandbox",
        description="``sandbox`` или ``production`` — выбирает FamilySearchConfig.",
    )
    fs_token_key: str = Field(
        default="",
        description=(
            "Fernet-ключ (32-байт base64url) для шифрования OAuth-токенов "
            "в users.fs_token_encrypted. Сгенерировать: "
            "``python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'``. Пустая строка = "
            "FS server-side OAuth отключён."
        ),
    )
    fs_oauth_state_ttl: int = Field(
        default=600,
        description="TTL Redis-ключа state (секунды). FamilySearch отдаёт callback ≤ 10 мин.",
    )
    fs_frontend_success_url: str = Field(
        default="http://localhost:3000/familysearch/connect?status=ok",
        description="Куда редиректить после успешного OAuth callback'а (фронт).",
    )
    fs_frontend_failure_url: str = Field(
        default="http://localhost:3000/familysearch/connect?status=error",
        description="Куда редиректить при OAuth ошибке.",
    )

    # ---- Phase 11.0 — sharing ----------------------------------------------
    public_base_url: str = Field(
        default="http://localhost:3000",
        description=(
            "Публичный URL фронта (без trailing slash). Используется для "
            "построения invite-link'ов: ``${public_base_url}/invitations/{token}``. "
            "В проде = `https://app.autotreegen.com`."
        ),
    )
    invitation_ttl_days: int = Field(
        default=14,
        ge=1,
        le=90,
        description="TTL приглашения в днях. По умолчанию 14 — баланс между удобством и риском.",
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
