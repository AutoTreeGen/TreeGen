"""OAuth 2.0 Authorization Code + PKCE flow для FamilySearch.

Phase 5.0 — заглушка интерфейса. Полная реализация (генерация code_verifier
/ code_challenge, POST на token endpoint, refresh) приходит в PR
``feat/phase-5.0-oauth-pkce`` (см. ADR-0011, Task 3 brief).

См. также:
- RFC 7636 (PKCE): https://datatracker.ietf.org/doc/html/rfc7636
- FamilySearch OAuth: https://developers.familysearch.org/docs/api/authentication
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import FamilySearchConfig


@dataclass(frozen=True, kw_only=True, slots=True)
class Token:
    """OAuth-токен FamilySearch.

    Attributes:
        access_token: Bearer-токен для ``Authorization`` header.
        refresh_token: Refresh-токен (native app — 90 days). ``None``,
            если FamilySearch не вернул его в ответе.
        expires_in: Срок жизни access_token в секундах от выдачи.
        scope: Скопы, которые реально были выданы (могут быть `<` запрошенных).
    """

    access_token: str
    refresh_token: str | None
    expires_in: int
    scope: str | None


class FamilySearchAuth:
    """OAuth 2.0 Authorization Code + PKCE flow для FamilySearch.

    Phase 5.0: только конструктор и поля. Методы ``start_flow``,
    ``complete_flow``, ``refresh`` появятся в Task 3 (отдельный PR).

    Args:
        client_id: App key, выданный FamilySearch developer program.
        config: Конфигурация endpoint'ов (sandbox/production).
            По умолчанию — sandbox, чтобы dev-код не уходил в production
            случайно.
    """

    def __init__(
        self,
        *,
        client_id: str,
        config: FamilySearchConfig | None = None,
    ) -> None:
        self.client_id = client_id
        self.config = config or FamilySearchConfig.sandbox()

    def __repr__(self) -> str:
        # Не светим client_id в repr — это не секрет, но и не нужно его
        # каждый раз показывать в логах. code_verifier / state в этот объект
        # не попадают (они возвращаются caller'у), так что repr безопасен.
        return f"FamilySearchAuth(environment={self.config.environment!r})"
