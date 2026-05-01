"""HTTP-клиент к AutoTreeGen API gateway.

Тонкая обёртка вокруг ``httpx.AsyncClient``: подставляет API-ключ в
заголовок, маппит HTTP-статусы в типизированные исключения, возвращает
сырые JSON-объекты (``dict``) — не парсит их в pydantic-модели.

Почему сырой dict, а не модели:

* MCP-host'у (Claude Desktop) всё равно нужен JSON-текст для LLM.
* API gateway эволюционирует — пере-описывать каждое поле в pydantic
  здесь означало бы держать схему в двух местах.
* Pydantic-валидация делается на стороне API gateway; MCP-серверу
  достаточно отличить 200/401/404/5xx и пробросить.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx

from .auth import ApiCredentials
from .config import TreeGenConfig


class ApiError(Exception):
    """Базовое исключение HTTP-клиента AutoTreeGen."""


class AuthError(ApiError):
    """API-ключ невалиден / истёк (HTTP 401/403)."""


class NotFoundError(ApiError):
    """Запрашиваемый ресурс не найден (HTTP 404)."""


class TreeGenClient:
    """Read-only async-клиент к AutoTreeGen API gateway.

    Args:
        config: Endpoint и таймауты.
        credentials: API-ключ пользователя.
        client: Опциональный собственный ``httpx.AsyncClient`` — для
            тестов с ``pytest-httpx``. Если передан, мы его не закрываем
            на ``__aexit__`` (separation of concerns).

    Usage:

    .. code-block:: python

        async with TreeGenClient(config=cfg, credentials=creds) as api:
            trees = await api.list_trees()
    """

    USER_AGENT = "treegen-mcp/0.1 (+https://github.com/anthropics/claude-code)"

    def __init__(
        self,
        *,
        config: TreeGenConfig,
        credentials: ApiCredentials,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._credentials = credentials
        self._owned_client = client is None
        self._client: httpx.AsyncClient | None = client

    async def __aenter__(self) -> TreeGenClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._config.timeout_seconds)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def __repr__(self) -> str:
        # Сам объект credentials уже маскирует ключ; здесь просто url.
        return f"TreeGenClient(api_url={self._config.api_url!r})"

    # -----------------------------------------------------------------
    # Tool wire-shapes — один метод на MCP tool.
    # Returns: сырой JSON, как пришёл от API gateway.
    # -----------------------------------------------------------------

    async def list_trees(self) -> dict[str, Any]:
        """``GET /trees`` — список деревьев пользователя."""
        return await self._get("/trees")

    async def get_tree_context(
        self,
        tree_id: str,
        *,
        anchor_person_id: str | None = None,
    ) -> dict[str, Any]:
        """``GET /trees/{tree_id}/context`` — context-pack для LLM.

        Args:
            tree_id: UUID дерева.
            anchor_person_id: Опциональный фокус — relative-references
                в context'е будут считаться от него (``my mother`` etc).
        """
        params: dict[str, str] = {}
        if anchor_person_id is not None:
            params["anchor_person_id"] = anchor_person_id
        return await self._get(f"/trees/{tree_id}/context", params=params)

    async def resolve_person(
        self,
        tree_id: str,
        reference: str,
        *,
        anchor_person_id: str | None = None,
    ) -> dict[str, Any]:
        """``POST /trees/{tree_id}/resolve-person`` — ego-resolver (ADR-0068).

        Args:
            tree_id: UUID дерева.
            reference: Натуральная фраза (``"my mother"``, ``"John Smith"``).
            anchor_person_id: От какой персоны считать relative-references.
        """
        body: dict[str, Any] = {"reference": reference}
        if anchor_person_id is not None:
            body["anchor_person_id"] = anchor_person_id
        return await self._post(f"/trees/{tree_id}/resolve-person", json=body)

    async def get_person(self, person_id: str) -> dict[str, Any]:
        """``GET /persons/{person_id}`` — карточка персоны."""
        return await self._get(f"/persons/{person_id}")

    async def search_persons(self, tree_id: str, query: str) -> dict[str, Any]:
        """``GET /trees/{tree_id}/persons/search?q={query}`` — name-search."""
        return await self._get(f"/trees/{tree_id}/persons/search", params={"q": query})

    # -----------------------------------------------------------------
    # Internal HTTP plumbing
    # -----------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        return {
            **self._credentials.auth_header(),
            "Accept": "application/json",
            "User-Agent": self.USER_AGENT,
        }

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, json=json)

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if self._client is None:
            msg = (
                "TreeGenClient must be used as async context manager "
                "or constructed with explicit client= kwarg."
            )
            raise RuntimeError(msg)
        url = f"{self._config.api_url}{path}"
        response = await self._client.request(
            method,
            url,
            headers=self._build_headers(),
            **kwargs,
        )
        _raise_for_api_status(response)
        data = response.json()
        if not isinstance(data, dict):
            msg = f"Expected JSON object from {path}, got {type(data).__name__}"
            raise ApiError(msg)
        return data


def _raise_for_api_status(response: httpx.Response) -> None:
    """Маппит HTTP status AutoTreeGen API в типизированное исключение."""
    if response.is_success:
        return
    status = response.status_code
    detail = f"AutoTreeGen API returned {status} {response.reason_phrase}"
    if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        raise AuthError(detail)
    if status == httpx.codes.NOT_FOUND:
        raise NotFoundError(detail)
    raise ApiError(detail)
