"""FamilySearch API client (Phase 5.0 skeleton).

Phase 5.0 — заглушка интерфейса. Реализация ``get_person()``, retry-middleware
и mapping HTTP status → исключения приходят в PR ``feat/phase-5.0-get-person``
(см. ADR-0011, Task 4 brief).
"""

from __future__ import annotations

from types import TracebackType

from .config import FamilySearchConfig

ACCEPT_HEADER = "application/x-fs-v1+json"


class FamilySearchClient:
    """Read-only async клиент FamilySearch API.

    Phase 5.0: только конструктор + async-context-manager протокол.
    Методы (``get_person``, ``search_persons``, ``get_pedigree``)
    появляются в последующих PR.

    Args:
        access_token: OAuth access token (получить через :class:`auth.FamilySearchAuth`).
        config: Endpoint config; по умолчанию — sandbox.
    """

    def __init__(
        self,
        *,
        access_token: str,
        config: FamilySearchConfig | None = None,
    ) -> None:
        self._access_token = access_token
        self.config = config or FamilySearchConfig.sandbox()

    async def __aenter__(self) -> FamilySearchClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # На Phase 5.0 ресурсов для cleanup нет. httpx.AsyncClient появится
        # в Task 4, тогда здесь будет его close().
        return None

    def __repr__(self) -> str:
        # access_token не попадает в repr — это секрет.
        return f"FamilySearchClient(environment={self.config.environment!r})"
