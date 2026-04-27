"""OAuth 2.0 Authorization Code + PKCE flow для FamilySearch.

См. ADR-0011 §«Auth — OAuth 2.0 Authorization Code + PKCE».

Поток (high-level):

1. ``start_flow(redirect_uri)`` — генерирует ``code_verifier``,
   ``code_challenge = SHA256(verifier)``, ``state`` (CSRF protection) и
   собирает authorize URL. Возвращает :class:`AuthorizationRequest` —
   caller открывает URL в браузере, юзер логинится, FamilySearch
   редиректит на ``redirect_uri?code=...&state=...``.
2. Caller проверяет ``state`` из callback против ``request.state``.
3. ``complete_flow(code, request, redirect_uri)`` — обменивает code
   на :class:`Token` через token endpoint с ``code_verifier``.
4. ``refresh(refresh_token)`` — обновляет access_token, когда expires_in
   подошёл.

Внешние ссылки:

- RFC 7636 (PKCE): https://datatracker.ietf.org/doc/html/rfc7636
- RFC 6749 (OAuth Authorization Code): https://datatracker.ietf.org/doc/html/rfc6749#section-4.1
- FamilySearch OAuth: https://developers.familysearch.org/docs/api/authentication
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import FamilySearchConfig
from .errors import AuthError, ClientError, FamilySearchError, ServerError

# RFC 7636 §4.1: code_verifier должен быть 43–128 символов из
# URL-safe alphabet. Берём 64 как хороший компромис: 64 chars ≈ 384 бит
# энтропии.
PKCE_VERIFIER_DEFAULT_LEN = 64
PKCE_VERIFIER_MIN_LEN = 43
PKCE_VERIFIER_MAX_LEN = 128


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


@dataclass(frozen=True, kw_only=True, slots=True)
class AuthorizationRequest:
    """Артефакты PKCE-запроса, которые caller хранит до callback.

    После того как пользователь авторизуется в браузере и FamilySearch
    редиректит на ``redirect_uri?code=...&state=...``, caller обязан:

    1. Сравнить ``state`` из callback с :attr:`state` (CSRF protection).
    2. Передать ``code`` и **этот** объект в
       :meth:`FamilySearchAuth.complete_flow`.
    """

    authorize_url: str
    code_verifier: str
    state: str


def _generate_code_verifier(length: int = PKCE_VERIFIER_DEFAULT_LEN) -> str:
    """Возвращает RFC 7636 code_verifier (43–128 chars из URL-safe alphabet)."""
    if not PKCE_VERIFIER_MIN_LEN <= length <= PKCE_VERIFIER_MAX_LEN:
        msg = (
            f"code_verifier length must be in "
            f"[{PKCE_VERIFIER_MIN_LEN}, {PKCE_VERIFIER_MAX_LEN}], got {length}"
        )
        raise ValueError(msg)
    # secrets.token_urlsafe возвращает base64url без padding; на 1 байт
    # приходится ~1.33 символа, так что берём с запасом и обрезаем до length.
    raw = secrets.token_urlsafe(length)
    return raw[:length]


def _code_challenge_from_verifier(verifier: str) -> str:
    """SHA256(verifier) → base64url без padding (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _generate_state() -> str:
    """CSRF-state — 32 байта энтропии в base64url."""
    return secrets.token_urlsafe(32)


def _parse_token_response(payload: dict[str, Any]) -> Token:
    """Маппит JSON ответ token endpoint'а в :class:`Token`.

    Падает с :class:`AuthError`, если в payload нет ``access_token``.
    """
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        msg = "token endpoint did not return access_token"
        raise AuthError(msg)
    expires_in = payload.get("expires_in", 0)
    if not isinstance(expires_in, int):
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 0
    return Token(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=expires_in,
        scope=payload.get("scope"),
    )


def _raise_for_token_error(response: httpx.Response) -> None:
    """Маппит HTTP-ошибку token endpoint'а в типизированное исключение.

    Валидный success — 200; иначе разбираемся.
    """
    if response.is_success:
        return
    status = response.status_code
    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        body = {}
    error_code = body.get("error", "unknown_error")
    error_desc = body.get("error_description", response.reason_phrase or "")
    detail = f"FamilySearch token endpoint returned {status} {error_code}: {error_desc}"
    if status in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
        raise AuthError(detail)
    if status == httpx.codes.BAD_REQUEST:
        # OAuth 2.0 RFC 6749 §5.2: 400 + error=invalid_grant — типичная
        # реакция на просроченный/повторно использованный code или
        # неверный refresh_token. Это auth-проблема, не bad request.
        if error_code in {"invalid_grant", "invalid_client", "unauthorized_client"}:
            raise AuthError(detail)
        raise ClientError(detail)
    if 500 <= status < 600:
        raise ServerError(detail)
    raise FamilySearchError(detail)


class FamilySearchAuth:
    """OAuth 2.0 Authorization Code + PKCE flow для FamilySearch.

    Args:
        client_id: App key, выданный FamilySearch developer program.
        config: Конфигурация endpoint'ов (sandbox/production).
            По умолчанию — sandbox, чтобы dev-код не уходил в production
            случайно.

    Note:
        Класс не хранит секретные значения (``code_verifier``, ``state``).
        Они возвращаются в :class:`AuthorizationRequest` и хранятся
        caller'ом — это сознательно, чтобы проще было сериализовать
        состояние flow в session storage веб-приложения.
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
        # каждый раз показывать в логах.
        return f"FamilySearchAuth(environment={self.config.environment!r})"

    def start_flow(
        self,
        *,
        redirect_uri: str,
        scope: str | None = None,
    ) -> AuthorizationRequest:
        """Готовит OAuth Authorization Code + PKCE запрос.

        Args:
            redirect_uri: URL, на который FamilySearch редиректит после
                логина. Должен совпадать с зарегистрированным в app.
            scope: Запрашиваемые scope'ы (через пробел). ``None`` — берём
                FamilySearch-дефолт.

        Returns:
            :class:`AuthorizationRequest` — caller открывает
            ``authorize_url`` в браузере, после callback использует
            ``state`` для CSRF-проверки и передаёт объект в
            :meth:`complete_flow`.
        """
        code_verifier = _generate_code_verifier()
        code_challenge = _code_challenge_from_verifier(code_verifier)
        state = _generate_state()

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if scope:
            params["scope"] = scope

        authorize_url = f"{self.config.authorize_url}?{urlencode(params)}"
        return AuthorizationRequest(
            authorize_url=authorize_url,
            code_verifier=code_verifier,
            state=state,
        )

    async def complete_flow(
        self,
        *,
        code: str,
        request: AuthorizationRequest,
        redirect_uri: str,
        client: httpx.AsyncClient | None = None,
    ) -> Token:
        """Обменивает authorization code на :class:`Token` через token endpoint.

        Args:
            code: ``code`` из callback'а (?code=...).
            request: Объект, который вернул :meth:`start_flow`. Используется
                ``code_verifier``; ``state`` должен быть проверен caller'ом
                до вызова — этот метод не валидирует CSRF за вас.
            redirect_uri: Тот же redirect_uri, что в start_flow. RFC 6749
                требует совпадения.
            client: Опциональный httpx.AsyncClient — чтобы прокинуть
                custom transport (для тестов через pytest-httpx).
        """
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": request.code_verifier,
        }
        return await self._post_token(data=data, client=client)

    async def refresh(
        self,
        *,
        refresh_token: str,
        client: httpx.AsyncClient | None = None,
    ) -> Token:
        """Обновляет access_token через refresh_token grant (RFC 6749 §6).

        Args:
            refresh_token: Refresh-токен, выданный ранее в :class:`Token`.
            client: Опциональный httpx.AsyncClient.
        """
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        return await self._post_token(data=data, client=client)

    async def _post_token(
        self,
        *,
        data: dict[str, str],
        client: httpx.AsyncClient | None,
    ) -> Token:
        """Общая POST-логика для authorize_code и refresh_token grants."""
        if client is None:
            async with httpx.AsyncClient() as owned_client:
                response = await owned_client.post(self.config.token_url, data=data)
        else:
            response = await client.post(self.config.token_url, data=data)
        _raise_for_token_error(response)
        return _parse_token_response(response.json())
