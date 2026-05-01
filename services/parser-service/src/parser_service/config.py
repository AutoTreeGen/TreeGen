"""Конфигурация parser-service через pydantic-settings.

Источники: переменные окружения, .env (через python-dotenv).
"""

from __future__ import annotations

from typing import Literal

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

    # ---- Clerk authentication (Phase 4.10, ADR-0033) ------------------------
    clerk_issuer: str = Field(
        default="",
        description=(
            "Clerk issuer URL (например, "
            "``https://accept-XXXX.clerk.accounts.dev``). Если пусто — "
            "auth-зависимости вернут 503 и endpoint'ы недоступны. Для "
            "тестов фикстуры монтируют свой issuer / JWKS."
        ),
    )
    clerk_jwks_url: str = Field(
        default="",
        description=(
            "Override JWKS URL. Пустая строка — берётся ``{issuer}/.well-known/jwks.json``."
        ),
    )
    clerk_audience: str = Field(
        default="",
        description=(
            "Optional ``aud``-claim. Пустая — пропускаем aud-проверку. "
            "Frontend tokens Clerk без custom audience не несут aud."
        ),
    )
    clerk_webhook_secret: str = Field(
        default="",
        description=(
            "Svix-секрет (Clerk webhook signing). Если пусто — "
            "``POST /webhooks/clerk`` отвергает все вызовы 503'ой."
        ),
    )

    # ---- Phase 4.11a — GDPR export (ADR-0046) ------------------------------
    export_signing_key: str = Field(
        default="",
        description=(
            "HMAC-секрет для подписи /users/me/requests/{id}/download "
            "токенов. Пустая строка — токен-валидация откатывается на raw "
            "user_id eq check (downgraded auth — нельзя в проде, тесты OK). "
            "Сгенерировать: `python -c 'import secrets;"
            " print(secrets.token_urlsafe(32))'`."
        ),
    )
    export_url_ttl_seconds: int = Field(
        default=900,
        ge=60,
        le=3600,
        description=(
            "TTL signed-URL'а для скачивания экспорта (15 мин по дефолту). "
            "Короткий — чтобы compromised email-пересылка не давала "
            "длинного окна доступа. Bucket-policy TTL длиннее (см. "
            "``export_object_ttl_days``), потому что user может запросить "
            "новый signed-URL пока сам zip ещё лежит."
        ),
    )
    export_object_ttl_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description=(
            "TTL самого ZIP-файла в bucket'е (дни). Применяется через "
            "object lifecycle policy на bucket-уровне (не из application). "
            "30 дней — стандарт GDPR retention для пользовательских "
            "exports (Art. 17 vs Art. 20 баланс)."
        ),
    )
    export_max_zip_size_mb: int = Field(
        default=500,
        ge=1,
        description=(
            "Soft-cap для размера сгенерированного ZIP. Если превышен, "
            "worker помечает request failed с пояснением — пользователь "
            "может попробовать subset через filter (Phase 4.11b) или "
            "связаться с support'ом."
        ),
    )

    # ---- Phase 10.2 — AI source extraction (ADR-0059) ----------------------
    # ``ai_layer.config.AILayerConfig`` читает свои переменные напрямую
    # (``AI_LAYER_ENABLED``, ``ANTHROPIC_API_KEY``, ``ANTHROPIC_MODEL``,
    # ``VOYAGE_*``) — здесь не дублируем, чтобы один источник правды.
    # Эти поля управляют parser-service-уровнем (budget, что caller
    # видит снаружи endpoint'ов).
    ai_max_runs_per_day: int = Field(
        default=10,
        ge=0,
        description=(
            "Per-user rate limit на AI source extraction. ``0`` = "
            "выключить rate limit (для dev). ADR-0059 default — 10/day."
        ),
    )
    ai_max_tokens_per_month: int = Field(
        default=100_000,
        ge=0,
        description=(
            "Per-user month-tokens budget на AI source extraction. ``0`` = "
            "выключить budget. ADR-0059 default — 100000/month."
        ),
    )
    extract_budget_usd: float = Field(
        default=0.50,
        ge=0.0,
        description=(
            "Phase 10.2b: per-source pre-flight cost cap в USD. До вызова "
            "Claude мы оцениваем стоимость (input_tokens × pricing × safety "
            "factor); если оценка > этого лимита — 429 ещё до запроса. "
            "Дополнение к per-user 24h/30d guards (которые ловят cumulative "
            "abuse) — этот предотвращает один разорительный документ. "
            "``0.0`` отключает per-source cap. Override через "
            "``PARSER_SERVICE_EXTRACT_BUDGET_USD``."
        ),
    )

    # ---- Phase 14.2 — internal service auth (digest worker → parser) -------
    internal_service_token: str = Field(
        default="",
        description=(
            "Shared secret для X-Internal-Service-Token validation на "
            "internal endpoint'ах parser-service (Phase 14.2: "
            "/users/{id}/digest-summary). Пустая строка → endpoint'ы "
            "возвращают 503 (отказ обслуживания), потому что без секрета "
            "любой может дёргать internal data о произвольном user'е."
        ),
    )

    # ---- Phase 10.9a — voice-to-tree (ADR-0064) ----------------------------
    # OPENAI_API_KEY, AI_DRY_RUN, WHISPER_* — unprefixed env vars: ключ
    # «глобальный» (не parser-service-specific), а Whisper-настройки
    # шарятся между parser-service worker'ом и потенциальным dna-service
    # voice-extension'ом в Phase 10.9.x. AUDIO_* — собственная зона
    # parser-service, но тоже unprefixed для симметрии с STORAGE_BACKEND
    # из shared_models.storage (см. ADR-0046 storage prefix-конвенцию).
    openai_api_key: str | None = Field(
        default=None,
        alias="OPENAI_API_KEY",
        description=(
            "OpenAI API key для Whisper STT. ``None`` + AI_DRY_RUN=true → "
            "mock-транскрипт без сетевых вызовов. ``None`` + AI_DRY_RUN=false → "
            "POST /audio-sessions возвращает 503 stt_unavailable. См. ADR-0064 §A1."
        ),
    )
    whisper_provider: Literal["openai", "self-hosted-whisper"] = Field(
        default="openai",
        alias="WHISPER_PROVIDER",
        description=(
            "Активный STT-провайдер. Phase 10.9a поддерживает только "
            "``openai``. ``self-hosted-whisper`` — privacy-tier опция "
            "Phase 10.9.x; пока не реализован. См. ADR-0064 §A2."
        ),
    )
    whisper_max_duration_sec: int = Field(
        default=600,
        ge=10,
        le=3600,
        alias="WHISPER_MAX_DURATION_SEC",
        description=(
            "Cap на длительность одной сессии (секунды). Cost-control: "
            "Whisper $0.006/мин × 10 мин = $0.06. См. ADR-0064 §«Cost runaway»."
        ),
    )
    audio_storage_bucket: str = Field(
        default="audio-sessions",
        alias="AUDIO_STORAGE_BUCKET",
        description=(
            "Bucket / GCS namespace для аудио-блобов. Отдельно от "
            "``STORAGE_BUCKET`` (gedcom/dna), чтобы lifecycle-policy для "
            "voice могла быть короче (audio удаляется per ADR-0064 §F1 "
            "сразу после успешной транскрипции; bucket-level retention — "
            "safety net на случай worker-краша)."
        ),
    )
    audio_max_size_bytes: int = Field(
        default=50_000_000,
        ge=1,
        alias="AUDIO_MAX_SIZE_BYTES",
        description=(
            "Cap на размер одного upload'а в байтах (default 50 MB). "
            "Валидируется в POST /audio-sessions; превышение → 413. "
            "Минимальный sanity-check ``ge=1`` — тесты могут опускать "
            "до 100 байт; production ставит ≥ 1 MB через env."
        ),
    )
    ai_dry_run: bool = Field(
        default=False,
        alias="AI_DRY_RUN",
        description=(
            "Dev/CI флаг: WhisperClient возвращает mock-транскрипт без "
            "OPENAI_API_KEY. ``true`` — endpoint принимает upload даже без "
            "ключа; ``false`` (production default) — без ключа 503."
        ),
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
