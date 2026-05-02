"""archive-service settings (pydantic-settings).

Префикс ENV — ``ARCHIVE_SERVICE_*`` для собственных полей; FamilySearch
clientId/Secret/RedirectURI используют **глобальные** имена
(``FAMILYSEARCH_*``), потому что эти credentials используются и в
parser-service (Phase 5.1), и в archive-service — общая переменная
окружения проще для деплоя.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация archive-service."""

    # -- FamilySearch (общие global-имена ENV) ---------------------------------
    familysearch_client_id: str = Field(
        default="",
        validation_alias="FAMILYSEARCH_CLIENT_ID",
        description="FS app-key. Пусто → /archives/familysearch/* отдают 503.",
    )
    familysearch_client_secret: str = Field(
        default="",
        validation_alias="FAMILYSEARCH_CLIENT_SECRET",
        description="Не используется в PKCE-flow; держим на случай будущего confidential-flow.",
    )
    familysearch_redirect_uri: str = Field(
        default="",
        validation_alias="FAMILYSEARCH_REDIRECT_URI",
        description="OAuth callback URL (должен совпадать с registered redirect).",
    )
    familysearch_base_url: str = Field(
        default="https://api.familysearch.org",
        validation_alias="FAMILYSEARCH_BASE_URL",
        description="Default = prod. Для sandbox — https://api-integ.familysearch.org.",
    )

    # -- archive-service-специфичные настройки ---------------------------------
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis для OAuth state, токенов at-rest и ETag-кэша.",
    )
    token_encryption_key: str = Field(
        default="",
        description=(
            "Fernet key (urlsafe-base64, 32 bytes). "
            "Пусто → /oauth/callback возвращает 503 (refusing to persist plaintext). "
            "Сгенерировать: ``python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'``."
        ),
    )
    fs_rate_limit_per_hour: int = Field(
        default=1500,
        description="FamilySearch quota — 1500 req/hour на партнёр-app.",
    )
    fs_rate_limit_burst: int = Field(
        default=60,
        description="Bucket capacity (burst). Default = час/25 ≈ 60 (1 минута worth).",
    )
    fs_cache_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        description="TTL для ETag-кэша (по умолчанию 24h).",
    )
    fs_oauth_state_ttl_seconds: int = Field(
        default=10 * 60,
        description="TTL для (state, code_verifier) между /oauth/start и /oauth/callback.",
    )

    # -- Clerk (Phase 4.10) ----------------------------------------------------
    clerk_issuer: str = Field(default="", description="Clerk issuer URL.")
    clerk_jwks_url: str = Field(default="", description="Override JWKS URL.")
    clerk_audience: str = Field(default="", description="Optional ``aud``.")

    # -- Admin gate (Phase 22.1) -----------------------------------------------
    # POST/PATCH/DELETE /archives/registry разрешены только для caller'а
    # с этим email в Clerk-claims. Mirrors parser-service.owner_email default.
    admin_email: str = Field(
        default="owner@autotreegen.local",
        description=(
            "Email caller'а, которому разрешены mutating-операции registry. "
            "Если ``ClerkClaims.email`` не совпадает — 403."
        ),
    )

    # -- Database (Phase 15.5) -------------------------------------------------
    # Используется планировщиком архивов для read-only-запросов к
    # events/citations/places (см. ``planner.repo``). Пусто → lifespan
    # пропускает init_engine, /archive-planner/* возвращает 503.
    database_url: str = Field(
        default="",
        description=(
            "Async DSN, например ``postgresql+asyncpg://user:pass@host:5432/db``. "
            "Пусто → planner-роутер недоступен."
        ),
    )

    debug: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_prefix="ARCHIVE_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def get_settings() -> Settings:
    """Получить инстанс настроек (pydantic-settings кеширует чтение env)."""
    return Settings()
