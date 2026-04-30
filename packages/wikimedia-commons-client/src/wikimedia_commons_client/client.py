"""Wikimedia Commons API client.

См. ADR-0058 §«Retry — tenacity на 429/503»: 3 попытки, exponential
backoff с jitter, retry на 429/503/network errors. На прочих 4xx —
никаких retry.

API surface ограничен Phase 9.1 use-case'ами:

* :meth:`search_by_coordinates` — geosearch (изображения в радиусе
  N метров от lat/lon). Применяется для Place'ов с известными координатами.
* :meth:`search_by_title` — full-text search по описаниям файлов.
  Fallback для Place'ов без координат.

Оба метода возвращают список :class:`models.CommonsImage` с уже
смерженными ``imageinfo`` + ``extmetadata`` полями. Разделение «search
→ get_metadata» намеренно скрыто внутри клиента: caller'у не нужно
знать, что MediaWiki Action API возвращает их разными частями ответа.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType
from typing import Any

import httpx
from pydantic import HttpUrl, TypeAdapter, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import WikimediaCommonsConfig
from .errors import (
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
    WikimediaCommonsError,
)
from .models import Attribution, CommonsImage, License

# Geosearch limits per MediaWiki API: gsradius 10..10000 м, gslimit 1..500.
# Берём conservative дефолты, чтобы один запрос не возвращал гигантский
# ответ (один Place = десятки изображений достаточно).
DEFAULT_GEOSEARCH_RADIUS_M = 5000
DEFAULT_LIMIT = 10
GEOSEARCH_MIN_RADIUS_M = 10
GEOSEARCH_MAX_RADIUS_M = 10_000
LIMIT_MIN = 1
LIMIT_MAX = 500

# Поля iiprop, которые мы запрашиваем у imageinfo. Минимально нужный
# набор — экономит трафик и снижает нагрузку на Commons.
IIPROP = "url|extmetadata|mime|size"

# Размер thumbnail, запрашиваемый у Commons (longest dimension в px).
DEFAULT_THUMB_WIDTH = 800

_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


@dataclass(frozen=True, kw_only=True, slots=True)
class RetryPolicy:
    """Политика retry для WikimediaCommonsClient.

    Default: 3 попытки, ``1s → ~2s → ~4s`` с jitter, потолок 30s.
    """

    max_attempts: int = 3
    initial_wait: float = 1.0
    max_wait: float = 30.0


class WikimediaCommonsClient:
    """Anonymous read-only async клиент Wikimedia Commons.

    Args:
        config: Endpoint + UA. По умолчанию — публичный production.
        retry_policy: Параметры retry. ``None`` → дефолт из :class:`RetryPolicy`.
        client: Опциональный собственный ``httpx.AsyncClient`` — для тестов
            (``pytest-httpx``). Если передан, ``__aexit__`` его не закрывает.

    Usage:

    .. code-block:: python

        async with WikimediaCommonsClient() as commons:
            images = await commons.search_by_coordinates(latitude=54.687, longitude=25.279)
    """

    def __init__(
        self,
        *,
        config: WikimediaCommonsConfig | None = None,
        retry_policy: RetryPolicy | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or WikimediaCommonsConfig()
        self.retry_policy = retry_policy or RetryPolicy()
        self._owned_client = client is None
        self._client: httpx.AsyncClient | None = client

    async def __aenter__(self) -> WikimediaCommonsClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.config.timeout)
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
        return f"WikimediaCommonsClient(api_url={self.config.api_url!r})"

    async def search_by_coordinates(
        self,
        *,
        latitude: float,
        longitude: float,
        radius_m: int = DEFAULT_GEOSEARCH_RADIUS_M,
        limit: int = DEFAULT_LIMIT,
    ) -> list[CommonsImage]:
        """Возвращает изображения в радиусе ``radius_m`` метров от ``(latitude, longitude)``.

        Args:
            latitude / longitude: Координаты в WGS84.
            radius_m: Радиус поиска, ``[10, 10000]``.
            limit: Максимум результатов, ``[1, 500]``.

        Raises:
            ValueError: ``radius_m`` или ``limit`` вне допустимого диапазона.
            RateLimitError / ServerError / ClientError / NotFoundError: см. модуль ``errors``.
        """
        _validate_radius(radius_m)
        _validate_limit(limit)
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "geosearch",
            "ggscoord": f"{latitude}|{longitude}",
            "ggsradius": str(radius_m),
            "ggslimit": str(limit),
            "ggsnamespace": "6",  # File: namespace — только медиа-страницы
            "prop": "imageinfo",
            "iiprop": IIPROP,
            "iiurlwidth": str(DEFAULT_THUMB_WIDTH),
            "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Credit|AttributionRequired",
        }
        return await self._search(params)

    async def search_by_title(
        self,
        *,
        query: str,
        limit: int = DEFAULT_LIMIT,
    ) -> list[CommonsImage]:
        """Full-text search по File-namespace.

        Args:
            query: Поисковая строка (название места, года и т.п.).
            limit: Максимум результатов, ``[1, 500]``.

        Raises:
            ValueError: пустой ``query`` или ``limit`` вне диапазона.
        """
        _validate_limit(limit)
        if not query.strip():
            msg = "query must be non-empty"
            raise ValueError(msg)
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": IIPROP,
            "iiurlwidth": str(DEFAULT_THUMB_WIDTH),
            "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Credit|AttributionRequired",
        }
        return await self._search(params)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    async def _search(self, params: dict[str, str]) -> list[CommonsImage]:
        response = await self._request("GET", params=params)
        payload = response.json()
        return _parse_search_response(payload)

    def _build_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        }

    async def _request(self, method: str, *, params: dict[str, str]) -> httpx.Response:
        """HTTP-запрос с retry на 429/503/network errors."""
        if self._client is None:
            msg = (
                "WikimediaCommonsClient must be used as async context manager "
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
                response = await client.request(
                    method, self.config.api_url, params=params, headers=headers
                )
                _raise_for_api_status(response)
                return response

        # tenacity reraise=True гарантирует, что досюда мы не дойдём.
        msg = "retrying exhausted without raising"  # pragma: no cover
        raise WikimediaCommonsError(msg)  # pragma: no cover


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def _validate_radius(radius_m: int) -> None:
    if not (GEOSEARCH_MIN_RADIUS_M <= radius_m <= GEOSEARCH_MAX_RADIUS_M):
        msg = (
            f"radius_m must be in [{GEOSEARCH_MIN_RADIUS_M}, {GEOSEARCH_MAX_RADIUS_M}], "
            f"got {radius_m}"
        )
        raise ValueError(msg)


def _validate_limit(limit: int) -> None:
    if not (LIMIT_MIN <= limit <= LIMIT_MAX):
        msg = f"limit must be in [{LIMIT_MIN}, {LIMIT_MAX}], got {limit}"
        raise ValueError(msg)


# ---------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------


def _parse_search_response(payload: dict[str, Any]) -> list[CommonsImage]:
    """Парсит MediaWiki Action API response в список ``CommonsImage``.

    Структура ответа (formatversion=2):

    .. code-block:: json

        {
          "query": {
            "pages": [
              {
                "pageid": 12345,
                "title": "File:Foo.jpg",
                "imageinfo": [{
                  "url": "...",
                  "thumburl": "...",
                  "descriptionurl": "...",
                  "width": 1024,
                  "height": 768,
                  "mime": "image/jpeg",
                  "extmetadata": {
                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                    "LicenseUrl": {"value": "https://..."},
                    "Credit": {"value": "<a href=...>...</a>"},
                    "AttributionRequired": {"value": "true"}
                  }
                }]
              }
            ]
          }
        }

    На пустой query.pages возвращается пустой список (это **не** ошибка —
    «нет картинок в радиусе» — нормальный исход).
    """
    if "error" in payload:
        # MediaWiki может вернуть 200 OK + error-объект (например при
        # некорректном generator-параметре). Маппим в ClientError —
        # это не retryable.
        err_info = payload.get("error") or {}
        info = err_info.get("info") or err_info.get("code") or "unknown"
        msg = f"Wikimedia API returned error: {info}"
        raise ClientError(msg)

    query = payload.get("query")
    if not query:
        return []
    pages = query.get("pages") or []
    if not isinstance(pages, list):
        # formatversion=1 возвращал dict — мы шлём fv=2 и не должны это видеть,
        # но защищаемся.
        return []

    results: list[CommonsImage] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        # Page может быть «missing» (Commons вернул metadata о несуществующем
        # title). Пропускаем.
        if page.get("missing"):
            continue
        title = page.get("title")
        imageinfo_list = page.get("imageinfo")
        if not title or not isinstance(imageinfo_list, list) or not imageinfo_list:
            continue
        info = imageinfo_list[0]
        parsed = _parse_imageinfo(title=title, info=info)
        if parsed is not None:
            results.append(parsed)
    return results


def _parse_imageinfo(*, title: str, info: dict[str, Any]) -> CommonsImage | None:
    """Парсит один imageinfo-блок. Возвращает ``None`` если URL'ы битые."""
    image_url_raw = info.get("url")
    page_url_raw = info.get("descriptionurl")
    if not image_url_raw or not page_url_raw:
        return None
    image_url = _to_http_url(image_url_raw)
    page_url = _to_http_url(page_url_raw)
    if image_url is None or page_url is None:
        return None

    thumb_url = _to_http_url(info.get("thumburl"))
    width = info.get("width") if isinstance(info.get("width"), int) else None
    height = info.get("height") if isinstance(info.get("height"), int) else None
    mime = info.get("mime") if isinstance(info.get("mime"), str) else None

    extmeta = info.get("extmetadata") or {}
    license_obj = _parse_license(extmeta) if isinstance(extmeta, dict) else None
    attribution = _parse_attribution(extmeta) if isinstance(extmeta, dict) else Attribution()

    try:
        return CommonsImage(
            title=title,
            page_url=page_url,
            image_url=image_url,
            thumb_url=thumb_url,
            width=width,
            height=height,
            mime=mime,
            license=license_obj,
            attribution=attribution,
        )
    except ValidationError:
        # Pydantic не пропустил sanity-check — возвращаем None, чтобы один
        # битый файл в ответе не валил всю выдачу.
        return None


def _parse_license(extmeta: dict[str, Any]) -> License | None:
    """Извлекает License из extmetadata. ``None`` если short_name отсутствует."""
    short_name = _extmeta_value(extmeta, "LicenseShortName")
    if not short_name:
        return None
    license_url = _to_http_url(_extmeta_value(extmeta, "LicenseUrl"))
    return License(short_name=short_name, url=license_url)


def _parse_attribution(extmeta: dict[str, Any]) -> Attribution:
    credit_html = _extmeta_value(extmeta, "Credit")
    required_raw = _extmeta_value(extmeta, "AttributionRequired")
    # Commons возвращает `"true"`/`"false"` строкой (не bool). Дефолт —
    # ``True``: лучше пере-показать credit, чем нарушить лицензию.
    required = required_raw.strip().lower() != "false" if isinstance(required_raw, str) else True
    return Attribution(credit_html=credit_html, required=required)


def _extmeta_value(extmeta: dict[str, Any], key: str) -> str | None:
    """Достаёт ``extmetadata[key].value`` со всеми защитами."""
    block = extmeta.get(key)
    if not isinstance(block, dict):
        return None
    value = block.get("value")
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _to_http_url(value: Any) -> HttpUrl | None:
    """Strict URL-валидация: не-URL → None (для парсинга чужих ответов)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return _HTTP_URL_ADAPTER.validate_python(value)
    except ValidationError:
        return None


# ---------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------


def _raise_for_api_status(response: httpx.Response) -> None:
    """Маппит HTTP status Commons API в типизированное исключение.

    - 404 → NotFoundError (non-retryable)
    - 429 → RateLimitError (retryable; retry_after из header'а)
    - 5xx → ServerError (retryable)
    - прочие 4xx (вкл. 401/403 от UA-policy) → ClientError (non-retryable)
    """
    if response.is_success:
        return
    status = response.status_code
    detail = f"Wikimedia API returned {status} {response.reason_phrase}"
    if status == httpx.codes.NOT_FOUND:
        raise NotFoundError(detail)
    if status == httpx.codes.TOO_MANY_REQUESTS:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        raise RateLimitError(detail, retry_after=retry_after)
    if 500 <= status < 600:
        raise ServerError(detail)
    if 400 <= status < 500:
        raise ClientError(detail)
    raise WikimediaCommonsError(detail)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
