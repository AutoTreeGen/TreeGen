"""Конфигурация endpoint'ов FamilySearch.

Дефолт — sandbox, чтобы dev-код не уходил в production случайно.
Production-конфиг создаётся явно (``FamilySearchConfig.production()``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True, slots=True)
class FamilySearchConfig:
    """Endpoints FamilySearch (sandbox или production).

    Attributes:
        api_base_url: Базовый URL REST API
            (например, ``https://api-integ.familysearch.org``).
        authorize_url: URL OAuth-страницы авторизации
            (открывается в браузере пользователя).
        token_url: URL token endpoint'а (POST для exchange/refresh).
        environment: ``"sandbox"`` или ``"production"`` — для логов и
            проверок в коде.
    """

    api_base_url: str
    authorize_url: str
    token_url: str
    environment: str

    @classmethod
    def sandbox(cls) -> FamilySearchConfig:
        """Конфигурация sandbox (integration) FamilySearch.

        Используется при разработке и в тестах. Production data сюда не
        попадает.
        """
        return cls(
            api_base_url="https://api-integ.familysearch.org",
            authorize_url=("https://identbeta.familysearch.org/cis-web/oauth2/v3/authorization"),
            token_url="https://identbeta.familysearch.org/cis-web/oauth2/v3/token",
            environment="sandbox",
        )

    @classmethod
    def production(cls) -> FamilySearchConfig:
        """Конфигурация production FamilySearch.

        Создаётся **только** для прод-окружения. Перед использованием
        убедиться, что app key одобрен FamilySearch для production.
        """
        return cls(
            api_base_url="https://api.familysearch.org",
            authorize_url=("https://ident.familysearch.org/cis-web/oauth2/v3/authorization"),
            token_url="https://ident.familysearch.org/cis-web/oauth2/v3/token",
            environment="production",
        )
