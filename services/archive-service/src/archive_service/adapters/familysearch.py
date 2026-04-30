"""FamilySearch read-only adapter (Phase 9.0 / ADR-0055).

Уровень service: оборачивает ``packages/familysearch-client`` (ADR-0011)
и добавляет:

* token-bucket rate-limit на ``(client_id, user_id)`` (FS quota = 1500/час);
* ETag-кэш ответов в Redis (24h по умолчанию);
* at-rest-encryption refresh-токенов (см. :mod:`archive_service.token_storage`);
* Pydantic-схемы ``RecordHit`` / ``PersonDetail`` для service-API.

Reuse:

* :class:`familysearch_client.FamilySearchAuth` — PKCE/refresh.
* :class:`familysearch_client.FamilySearchConfig` — endpoints (sandbox/prod).

Адаптер сам не работает с :class:`FamilySearchClient` (Tree-API client),
потому что нам нужен **HTTP-уровень** для ETag-кэша; парсинг GEDCOM-X
у нас тоньше — service экспортирует только то, что нужно фронту.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
import redis.asyncio as redis_asyncio
from familysearch_client import (
    AuthError,
    AuthorizationRequest,
    ClientError,
    FamilySearchAuth,
    FamilySearchConfig,
    FamilySearchError,
    NotFoundError,
    RateLimitError,
    ServerError,
    Token,
)

from archive_service.config import Settings

ACCEPT_HEADER: Final = "application/x-fs-v1+json"
RECORDS_SEARCH_PATH: Final = "/platform/records/search"
TREE_PERSON_PATH_TEMPLATE: Final = "/platform/tree/persons/{fsid}"
OAUTH_STATE_KEY_PREFIX: Final = "fs:oauth_state"
RATE_LIMIT_KEY_TEMPLATE: Final = "fs:rate:{client_id}:{user_id}"
CACHE_KEY_TEMPLATE: Final = "fs:cache:{cache_key}"


class AdapterRateLimitError(FamilySearchError):
    """Rate-limit нашего token-bucket'а (отдельный от FS-429).

    Поднимается, когда локальный bucket отказал ещё до отправки запроса
    в FS. Endpoint конвертирует в HTTP 429 с ``Retry-After``.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True, kw_only=True, slots=True)
class RecordHit:
    """Один результат FS Records Search (упрощённое представление)."""

    fsid: str | None
    title: str
    summary: str | None
    score: float | None
    persons: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True, slots=True)
class PersonDetail:
    """Persona из FS Tree API (упрощённое представление)."""

    fsid: str
    full_name: str | None
    gender: str | None
    facts: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def make_fs_config(base_url: str) -> FamilySearchConfig:
    """Получить :class:`FamilySearchConfig` под заданный base_url.

    ``base_url``-startswith ``api-integ`` → sandbox, иначе — production.
    Это эвристика, но достаточно надёжная: FS использует ровно эти два
    окружения, и UNIT-тесты подменяют base_url на ``http://test`` (тогда
    sandbox-форма, что для тестов безопасно).
    """
    if "integ" in base_url or base_url.startswith("http://test"):
        sandbox = FamilySearchConfig.sandbox()
        return dataclasses.replace(sandbox, api_base_url=base_url)
    prod = FamilySearchConfig.production()
    return dataclasses.replace(prod, api_base_url=base_url)


def _hash_params(endpoint: str, params: dict[str, Any]) -> str:
    """SHA256(endpoint + canonical-JSON params) — детерминированный cache_key."""
    payload = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _drop_none(items: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    """Собрать dict, выбрасывая None-значения (для query-параметров)."""
    return {k: v for k, v in items if v is not None}


def _raise_for_status(response: httpx.Response) -> None:
    """HTTP-status FamilySearch → исключение из familysearch_client.errors."""
    if response.is_success:
        return
    status_code = response.status_code
    detail = f"FamilySearch returned {status_code} {response.reason_phrase}"
    if status_code in {401, 403}:
        raise AuthError(detail)
    if status_code == 404:
        raise NotFoundError(detail)
    if status_code == 429:
        retry_after_raw = response.headers.get("Retry-After")
        retry_after: float | None
        try:
            retry_after = float(retry_after_raw) if retry_after_raw else None
        except ValueError:
            retry_after = None
        raise RateLimitError(detail, retry_after=retry_after)
    if 500 <= status_code < 600:
        raise ServerError(detail)
    if 400 <= status_code < 500:
        raise ClientError(detail)
    raise FamilySearchError(detail)


class FamilySearchAdapter:
    """Сервисный адаптер FamilySearch.

    Управление жизненным циклом httpx-клиента — на caller'е:

    * если передан ``http_client``, адаптер его не закрывает;
    * если ``None``, адаптер открывает per-request клиента
      (``async with httpx.AsyncClient()``).

    Args:
        settings: Конфиг сервиса.
        redis: Async Redis-клиент (decode_responses=True).
        http_client: Опциональный httpx.AsyncClient (для тестов/моков).
    """

    def __init__(
        self,
        *,
        settings: Settings,
        redis: redis_asyncio.Redis,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis
        self._http = http_client
        self._fs_config = make_fs_config(settings.familysearch_base_url)
        self._auth = FamilySearchAuth(
            client_id=settings.familysearch_client_id,
            config=self._fs_config,
        )

    # ------------------------------------------------------------------
    # OAuth 2.0 PKCE flow
    # ------------------------------------------------------------------

    def start_authorize(
        self,
        *,
        redirect_uri: str,
        scope: str | None = None,
    ) -> AuthorizationRequest:
        """Сгенерировать PKCE-параметры и authorize URL.

        Caller должен сохранить ``code_verifier`` (через
        :meth:`save_oauth_state`) до callback'а.
        """
        return self._auth.start_flow(redirect_uri=redirect_uri, scope=scope)

    async def save_oauth_state(self, request: AuthorizationRequest) -> None:
        """Положить code_verifier в Redis под ключом state (TTL — settings)."""
        key = f"{OAUTH_STATE_KEY_PREFIX}:{request.state}"
        await self._redis.set(
            key,
            request.code_verifier,
            ex=self._settings.fs_oauth_state_ttl_seconds,
        )

    async def consume_oauth_state(self, state: str) -> str | None:
        """Атомарно достать и удалить code_verifier для state.

        Возвращает ``None`` если state не найден (CSRF protection: отказ).
        """
        key = f"{OAUTH_STATE_KEY_PREFIX}:{state}"
        # GETDEL — атомарно прочитать-и-удалить (Redis 6.2+, fakeredis 2.x +).
        value = await self._redis.getdel(key)
        if value is None:
            return None
        # decode_responses=True → str.
        return value if isinstance(value, str) else value.decode("utf-8")

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> Token:
        """Обменять authorization_code → :class:`Token`.

        ``code_verifier`` обязан совпадать с тем, что вернул ``start_flow``.
        Сам state-check мы уже сделали в :meth:`consume_oauth_state`.
        """
        request = AuthorizationRequest(
            authorize_url="",  # уже не нужен
            code_verifier=code_verifier,
            state="",  # уже проверен caller'ом
        )
        return await self._auth.complete_flow(
            code=code,
            request=request,
            redirect_uri=redirect_uri,
            client=self._http,
        )

    async def refresh(self, *, refresh_token: str) -> Token:
        return await self._auth.refresh(
            refresh_token=refresh_token,
            client=self._http,
        )

    # ------------------------------------------------------------------
    # Records / Tree API (read-only proxy)
    # ------------------------------------------------------------------

    async def search_records(
        self,
        *,
        access_token: str,
        user_id: str,
        query: str | None = None,
        surname: str | None = None,
        given: str | None = None,
        year: int | None = None,
        year_range: int = 5,
    ) -> list[RecordHit]:
        """Прокси к FS Records Search API.

        Параметры превращаются в FS-нативные (``q.givenName``, ``q.surname``,
        ``q.birthLikeDate``). FS возвращает Atom-style JSON; парсим его в
        :class:`RecordHit`.
        """
        await self._enforce_rate_limit(user_id=user_id)
        params = self._build_search_params(
            query=query,
            surname=surname,
            given=given,
            year=year,
            year_range=year_range,
        )
        body = await self._cached_get(
            access_token=access_token,
            endpoint=RECORDS_SEARCH_PATH,
            params=params,
        )
        return self._parse_search_response(body)

    async def get_person(
        self,
        *,
        access_token: str,
        user_id: str,
        fsid: str,
    ) -> PersonDetail:
        """Прокси к FS Tree API ``/persons/{id}``."""
        await self._enforce_rate_limit(user_id=user_id)
        endpoint = TREE_PERSON_PATH_TEMPLATE.format(fsid=fsid)
        body = await self._cached_get(
            access_token=access_token,
            endpoint=endpoint,
            params={},
        )
        return self._parse_person_response(body, fsid)

    # ------------------------------------------------------------------
    # Internal — rate-limit, cache, http
    # ------------------------------------------------------------------

    async def _enforce_rate_limit(self, *, user_id: str) -> None:
        """Token-bucket: 1 токен на запрос, refill = quota_per_hour / 3600 в сек.

        Реализация — read/compute/write через ``HMGET`` + ``HSET``, без Lua
        (fakeredis в тестах не реализует EVAL без lupa-extras). При гонке
        между N-instances Cloud Run возможен overshoot на 1–2 запроса —
        для FS-квоты 1500/час это пренебрежимо.

        Capacity = ``fs_rate_limit_burst`` (default 60). При исчерпании —
        :class:`AdapterRateLimitError` c estimated retry_after.
        """
        capacity = self._settings.fs_rate_limit_burst
        per_hour = max(1, self._settings.fs_rate_limit_per_hour)
        refill_rate = per_hour / 3600.0
        ttl = max(2 * 3600, int(capacity / refill_rate) + 60) if refill_rate > 0 else 7200
        key = RATE_LIMIT_KEY_TEMPLATE.format(
            client_id=self._settings.familysearch_client_id or "_unset",
            user_id=user_id,
        )
        now = time.time()

        tokens_raw, last_raw = await self._redis.hmget(key, "tokens", "last_refill")
        tokens = float(tokens_raw) if tokens_raw is not None else float(capacity)
        last_refill = float(last_raw) if last_raw is not None else now
        delta = max(0.0, now - last_refill)
        tokens = min(float(capacity), tokens + delta * refill_rate)
        if tokens < 1.0:
            # Persist refill часть, чтобы при следующем вызове накопление
            # продолжилось от ``now``, а не от старого ``last_refill``.
            await self._redis.hset(
                key,
                mapping={"tokens": f"{tokens:.6f}", "last_refill": f"{now:.6f}"},
            )
            await self._redis.expire(key, ttl)
            retry_after = 1.0 / refill_rate if refill_rate > 0 else None
            msg = (
                f"archive-service local rate-limit hit for user_id={user_id!r}; "
                f"capacity={capacity}, per_hour={per_hour}."
            )
            raise AdapterRateLimitError(msg, retry_after=retry_after)
        tokens -= 1.0
        await self._redis.hset(
            key,
            mapping={"tokens": f"{tokens:.6f}", "last_refill": f"{now:.6f}"},
        )
        await self._redis.expire(key, ttl)

    async def _cached_get(
        self,
        *,
        access_token: str,
        endpoint: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """GET с ETag-кэшем в Redis. На 304 возвращает закэшированный body."""
        cache_key = _hash_params(endpoint, params)
        redis_key = CACHE_KEY_TEMPLATE.format(cache_key=cache_key)
        cached = await self._redis.hgetall(redis_key)
        cached_etag = cached.get("etag") if cached else None

        url = f"{self._fs_config.api_base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": ACCEPT_HEADER,
        }
        if cached_etag:
            headers["If-None-Match"] = cached_etag

        response = await self._do_get(url=url, params=params, headers=headers)

        if response.status_code == 304 and cached:
            return self._json_loads(cached["body"])

        _raise_for_status(response)
        body: dict[str, Any] = response.json()
        new_etag = response.headers.get("etag") or response.headers.get("ETag")
        if new_etag:
            await self._redis.hset(
                redis_key,
                mapping={"etag": new_etag, "body": json.dumps(body)},
            )
            await self._redis.expire(redis_key, self._settings.fs_cache_ttl_seconds)
        return body

    async def _do_get(
        self,
        *,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        """Выполнить GET. Если caller передал ``http_client`` — используем его."""
        if self._http is not None:
            return await self._http.get(url, params=params, headers=headers)
        async with httpx.AsyncClient() as client:
            return await client.get(url, params=params, headers=headers)

    @staticmethod
    def _json_loads(text: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(text)
        return loaded

    @staticmethod
    def _build_search_params(
        *,
        query: str | None,
        surname: str | None,
        given: str | None,
        year: int | None,
        year_range: int,
    ) -> dict[str, Any]:
        """Превратить service-API в FS native query-params."""
        q_parts: list[str] = []
        if given:
            q_parts.append(f'givenName:"{given}"')
        if surname:
            q_parts.append(f'surname:"{surname}"')
        if year is not None:
            from_year = year - max(0, year_range)
            to_year = year + max(0, year_range)
            q_parts.append(f"birthLikeDate:from {from_year} to {to_year}")
        if query:
            q_parts.append(query)
        return _drop_none(
            (
                ("q", " ".join(q_parts) if q_parts else None),
                # FS поддерживает count/start; service-уровень фиксирует разумные default'ы.
                ("count", "20"),
            ),
        )

    @staticmethod
    def _parse_search_response(body: dict[str, Any]) -> list[RecordHit]:
        """FS Records Search возвращает GEDCOM-X-style JSON.

        Нас интересует ``entries[]``: id, title, score, content.gedcomx.persons[].
        Парсер консервативный: пропускает entry без title.
        """
        hits: list[RecordHit] = []
        entries = body.get("entries") or []
        if not isinstance(entries, list):
            return hits
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            if not isinstance(title, str):
                continue
            persons_obj = (entry.get("content") or {}).get("gedcomx", {}).get("persons", [])
            persons = persons_obj if isinstance(persons_obj, list) else []
            score_raw = entry.get("score")
            score: float | None
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            summary_raw = entry.get("summary")
            summary = summary_raw if isinstance(summary_raw, str) else None
            fsid_raw = entry.get("id")
            fsid = fsid_raw if isinstance(fsid_raw, str) else None
            hits.append(
                RecordHit(
                    fsid=fsid,
                    title=title,
                    summary=summary,
                    score=score,
                    persons=persons,
                ),
            )
        return hits

    @staticmethod
    def _parse_person_response(body: dict[str, Any], fsid: str) -> PersonDetail:
        """FS Tree API возвращает GEDCOM-X с массивом ``persons``.

        Берём первый person (FS возвращает focus person первым). Если
        ``persons`` пуст — это считаем NotFound на парсе (но 404 от
        FS уже бы превратился в :class:`NotFoundError` ранее).
        """
        persons = body.get("persons") or []
        if not isinstance(persons, list) or not persons:
            msg = f"FamilySearch /persons/{fsid} returned no person body."
            raise NotFoundError(msg)
        person = persons[0]
        names = person.get("names") or []
        full_name: str | None = None
        if isinstance(names, list) and names:
            forms = (names[0] or {}).get("nameForms") or []
            if isinstance(forms, list) and forms:
                full_name_raw = forms[0].get("fullText")
                full_name = full_name_raw if isinstance(full_name_raw, str) else None
        gender_raw = (person.get("gender") or {}).get("type")
        gender = gender_raw if isinstance(gender_raw, str) else None
        facts_raw = person.get("facts") or []
        facts = facts_raw if isinstance(facts_raw, list) else []
        return PersonDetail(
            fsid=fsid,
            full_name=full_name,
            gender=gender,
            facts=facts,
            raw=person,
        )


def quota_configured(settings: Settings) -> bool:
    """Проверка: достаточно ли env, чтобы /search и /person вообще что-то делать."""
    return bool(
        settings.familysearch_client_id and settings.familysearch_redirect_uri,
    )
