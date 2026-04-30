"""dna-service settings (pydantic-settings).

ENV-префикс `DNA_SERVICE_` — например `DNA_SERVICE_DATABASE_URL`,
`DNA_SERVICE_STORAGE_ROOT`. См. ADR-0020 для описания каждой настройки.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация dna-service."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )
    storage_root: Path = Field(
        default=Path("var/dna-blobs"),
        description="Каталог для encrypted blob-файлов (относительно cwd или абсолютный).",
    )
    require_encryption: bool = Field(
        default=True,
        description=(
            "Если True (prod default) — отвергаем plaintext uploads. "
            "При False (dev/CI) сервис принимает любой контент и помечает "
            "DnaTestRecord.encryption_scheme='none'. См. ADR-0020."
        ),
    )
    max_upload_mb: int = Field(
        default=100,
        description="Лимит размера upload в мегабайтах (один DNA-blob).",
    )
    debug: bool = Field(default=False)

    # ---- Cache (Phase 6.4 / ADR-0054) ---------------------------------------
    redis_url: str = Field(
        default="",
        description=(
            "Optional Redis URL для cache compute-heavy ответов "
            "(Phase 6.4 — triangulation). Пусто → no-op cache (всегда recompute)."
        ),
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
        env_prefix="DNA_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def get_settings() -> Settings:
    """Получить инстанс настроек (pydantic-settings кеширует чтение env)."""
    return Settings()
