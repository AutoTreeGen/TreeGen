"""billing-service settings (pydantic-settings).

ENV-префикс ``BILLING_SERVICE_``. См. README §«ENV-настройки».
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация billing-service."""

    database_url: str = Field(
        default="postgresql+asyncpg://autotreegen:autotreegen@localhost:5433/autotreegen",
        description="Async-DSN postgres.",
    )

    # ---- Stripe ----
    stripe_api_key: str = Field(
        default="",
        description="sk_test_* / sk_live_* — API key для Stripe SDK.",
    )
    stripe_webhook_secret: str = Field(
        default="",
        description="whsec_* — секрет для верификации webhook-подписи.",
    )
    stripe_price_pro: str = Field(
        default="",
        description="price_* — Stripe Price ID Pro-плана.",
    )

    # ---- Redirect URLs ----
    checkout_success_url: str = Field(
        default="http://localhost:3000/settings/billing?checkout=success",
        description="URL, куда Stripe редиректит после успешного чекаута.",
    )
    checkout_cancel_url: str = Field(
        default="http://localhost:3000/pricing?checkout=cancel",
        description="URL, куда Stripe редиректит при cancel'е чекаута.",
    )
    portal_return_url: str = Field(
        default="http://localhost:3000/settings/billing",
        description="URL, куда Customer Portal вернёт пользователя.",
    )

    # ---- Feature flag ----
    billing_enabled: bool = Field(
        default=True,
        description=(
            "Если False — все entitlement-проверки пропускают, "
            "get_user_plan возвращает PRO. Для local dev и CI."
        ),
    )

    # ---- Grace period ----
    past_due_grace_days: int = Field(
        default=7,
        ge=0,
        description=(
            "Сколько дней после past_due-события мы продолжаем давать "
            "доступ к фичам Pro. См. ADR-0034 §«Failed payment policy»."
        ),
    )

    debug: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_prefix="BILLING_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton-доступ к настройкам.

    Кешируем на уровень процесса — env-переменные читаются один раз.
    Для тестов: ``get_settings.cache_clear()`` после ``monkeypatch.setenv``.
    """
    return Settings()
