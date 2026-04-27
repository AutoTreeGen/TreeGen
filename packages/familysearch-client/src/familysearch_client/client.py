"""FamilySearch API client.

См. ADR-0011 §«Retry — tenacity на 429/503»: 3 попытки, exponential
backoff с jitter, retry на 429/503/network errors. На 4xx-кроме-429 —
никаких retry (это либо bug в нашем запросе, либо валидный отказ).
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from . import _mapping
from .config import FamilySearchConfig
from .errors import (
    AuthError,
    ClientError,
    FamilySearchError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from .models import FsPerson

ACCEPT_HEADER = "application/x-fs-v1+json"


@dataclass(frozen=True, kw_only=True, slots=True)
class RetryPolicy:
    """Политика retry для FamilySearchClient.

    Attributes:
        max_attempts: Сколько всего попыток (включая первую). 1 = без retry.
        initial_wait: Первая задержка перед retry, секунды.
        max_wait: Потолок задержки, секунды (после exponential backoff).

    Default: 3 попытки, ``1s → ~2s → ~4s`` с jitter, потолок 30s
    (см. ADR-0011 §«Retry — tenacity на 429/503»).
    """

    max_attempts: int = 3
    initial_wait: float = 1.0
    max_wait: float = 30.0


class FamilySearchClient:
    """Read-only async клиент FamilySearch API.

    Args:
        access_token: OAuth access token (получить через :class:`auth.FamilySearchAuth`).
        config: Endpoint config; по умолчанию — sandbox.
        retry_policy: Параметры retry. ``None`` → дефолт из :class:`RetryPolicy`.
        client: Опциональный собственный ``httpx.AsyncClient`` — для тестов
            (``pytest-httpx``) или прокидывания custom transport. Если
            передан, вызовы не закрывают его на ``__aexit__``.

    Usage:

    .. code-block:: python

        async with FamilySearchClient(access_token=token) as fs:
            person = await fs.get_person("KW7S-VQJ")
    """

    def __init__(
        self,
        *,
        access_token: str,
        config: FamilySearchConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._access_token = access_token
        self.config = config or FamilySearchConfig.sandbox()
        self.retry_policy = retry_policy or RetryPolicy()
        # Если caller передал свой client — мы его не закрываем
        # (separation of concerns: владелец ресурса = его и закрывает).
        self._owned_client = client is None
        self._client: httpx.AsyncClient | None = client

    async def __aenter__(self) -> FamilySearchClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
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
        # access_token не попадает в repr — это секрет.
        return f"FamilySearchClient(environment={self.config.environment!r})"

    async def get_person(self, person_id: str) -> FsPerson:
        """Возвращает :class:`FsPerson` для FamilySearch person ID.

        Args:
            person_id: ID вида ``KW7S-VQJ``.

        Raises:
            NotFoundError: 404 — person не существует или unaccessible.
            AuthError: 401/403 — токен битый/expired/недостаточно scopes.
            RateLimitError: 429 после исчерпания retry'ев. Содержит
                ``retry_after`` если FamilySearch вернул соответствующий
                header.
            ServerError: 5xx после исчерпания retry'ев.
            ClientError: Прочие 4xx (400/409/422 и т.п.).
        """
        url = f"{self.config.api_base_url}/platform/tree/persons/{person_id}"
        response = await self._request("GET", url)
        return _mapping.parse_person_response(response.json())

    # -----------------------------------------------------------------
    # Internal HTTP plumbing
    # -----------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": ACCEPT_HEADER,
        }

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Выполняет HTTP-запрос с retry на 429/503/network errors.

        Не-retryable ошибки (401/403/404/прочие 4xx) поднимаются сразу.
        """
        if self._client is None:
            msg = (
                "FamilySearchClient must be used as async context manager "
                "or constructed with explicit client= kwarg."
            )
            raise RuntimeError(msg)
        client = self._client
        headers = self._build_headers()

        retrying = AsyncRetrying(
            retry=retry_if_exception_type((RateLimitError, ServerError, httpx.NetworkError)),
            stop=stop_after_attempt(self.retry_policy.max_attempts),
            wait=wait_exponential_jitter(
                initial=self.retry_policy.initial_wait,
                max=self.retry_policy.max_wait,
            ),
            reraise=True,
        )

        async for attempt in retrying:
            with attempt:
                response = await client.request(method, url, headers=headers, **kwargs)
                _raise_for_api_status(response)
                return response

        # tenacity reraise=True гарантирует, что досюда мы не дойдём, но
        # mypy этого не знает.
        msg = "retrying exhausted without raising"  # pragma: no cover
        raise FamilySearchError(msg)  # pragma: no cover


def _raise_for_api_status(response: httpx.Response) -> None:
    """Маппит HTTP status FamilySearch API в типизированное исключение.

    Не-success → исключение по таблице:

    - 401, 403 → AuthError (non-retryable)
    - 404 → NotFoundError (non-retryable)
    - 429 → RateLimitError (retryable; retry_after из header'а)
    - 5xx → ServerError (retryable)
    - прочие 4xx → ClientError (non-retryable)
    """
    if response.is_success:
        return
    status = response.status_code
    detail = f"FamilySearch API returned {status} {response.reason_phrase}"
    if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        raise AuthError(detail)
    if status == httpx.codes.NOT_FOUND:
        raise NotFoundError(detail)
    if status == httpx.codes.TOO_MANY_REQUESTS:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise RateLimitError(detail, retry_after=retry_after)
    if 500 <= status < 600:
        raise ServerError(detail)
    if 400 <= status < 500:
        raise ClientError(detail)
    raise FamilySearchError(detail)


def _parse_retry_after(value: str | None) -> float | None:
    """Парсит ``Retry-After`` header (секунды). HTTP-date форма не парсится —
    FamilySearch её на практике не использует, так что не усложняем.
    """
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
