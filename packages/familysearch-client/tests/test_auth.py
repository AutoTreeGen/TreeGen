"""Тесты для FamilySearchAuth — OAuth 2.0 PKCE flow.

Все HTTP-вызовы мокаются через ``pytest-httpx``. Реальные тесты на sandbox
помечаются ``@pytest.mark.familysearch_real`` и не входят в Phase 5.0
(нужен sandbox app key — отдельная задача владельцу).
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from familysearch_client import (
    AuthError,
    AuthorizationRequest,
    FamilySearchAuth,
    FamilySearchConfig,
)
from familysearch_client.auth import (
    PKCE_VERIFIER_MAX_LEN,
    PKCE_VERIFIER_MIN_LEN,
    _code_challenge_from_verifier,
    _generate_code_verifier,
)
from pytest_httpx import HTTPXMock

# RFC 7636 §4.1: alphabet — A-Z a-z 0-9 - . _ ~
_VERIFIER_ALPHABET = re.compile(r"^[A-Za-z0-9\-._~]+$")
_REDIRECT = "http://localhost:8765/cb"


def test_auth_imports_and_constructs() -> None:
    """FamilySearchAuth конструируется с дефолтным sandbox-конфигом."""
    auth = FamilySearchAuth(client_id="test-app-key")
    assert auth.client_id == "test-app-key"
    assert auth.config.environment == "sandbox"


def test_auth_repr_does_not_leak_client_id() -> None:
    """repr() не содержит client_id (минимум — не падает в логах)."""
    auth = FamilySearchAuth(client_id="should-not-appear")
    assert "should-not-appear" not in repr(auth)


def test_auth_accepts_explicit_production_config() -> None:
    """Production endpoints конструируются явным вызовом."""
    config = FamilySearchConfig.production()
    auth = FamilySearchAuth(client_id="prod-key", config=config)
    assert auth.config.environment == "production"
    assert auth.config.api_base_url == "https://api.familysearch.org"


# ---------------------------------------------------------------------------
# PKCE primitives
# ---------------------------------------------------------------------------


def test_generated_code_verifier_obeys_rfc7636() -> None:
    """code_verifier — 43–128 chars из URL-safe alphabet."""
    verifier = _generate_code_verifier()
    assert PKCE_VERIFIER_MIN_LEN <= len(verifier) <= PKCE_VERIFIER_MAX_LEN
    assert _VERIFIER_ALPHABET.match(verifier)


def test_generated_code_verifier_is_random() -> None:
    """Два вызова возвращают разные verifier'ы (ничтожный шанс коллизии)."""
    a = _generate_code_verifier()
    b = _generate_code_verifier()
    assert a != b


def test_generate_code_verifier_rejects_invalid_length() -> None:
    """Длина вне [43, 128] — ValueError."""
    with pytest.raises(ValueError, match="code_verifier length"):
        _generate_code_verifier(length=10)
    with pytest.raises(ValueError, match="code_verifier length"):
        _generate_code_verifier(length=200)


def test_code_challenge_is_sha256_base64url_no_padding() -> None:
    """code_challenge = base64url(SHA256(verifier)) без '=' padding'а."""
    verifier = "a" * 64
    challenge = _code_challenge_from_verifier(verifier)
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert challenge == expected
    assert "=" not in challenge


# ---------------------------------------------------------------------------
# start_flow
# ---------------------------------------------------------------------------


def test_start_flow_returns_authorization_request_with_distinct_fields() -> None:
    """start_flow возвращает AuthorizationRequest с непустыми полями."""
    auth = FamilySearchAuth(client_id="app-key")
    request = auth.start_flow(redirect_uri=_REDIRECT)
    assert isinstance(request, AuthorizationRequest)
    assert request.code_verifier
    assert request.state
    assert request.code_verifier != request.state
    assert request.authorize_url.startswith(auth.config.authorize_url)


def test_start_flow_url_contains_pkce_and_state_params() -> None:
    """URL содержит client_id, redirect_uri, code_challenge S256, state."""
    auth = FamilySearchAuth(client_id="app-key")
    request = auth.start_flow(redirect_uri=_REDIRECT, scope="openid offline_access")

    parsed = urlparse(request.authorize_url)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert params["response_type"] == "code"
    assert params["client_id"] == "app-key"
    assert params["redirect_uri"] == _REDIRECT
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == _code_challenge_from_verifier(request.code_verifier)
    assert params["state"] == request.state
    assert params["scope"] == "openid offline_access"


def test_start_flow_two_invocations_produce_distinct_state() -> None:
    """Каждый вызов start_flow генерирует свежие state и verifier."""
    auth = FamilySearchAuth(client_id="app-key")
    a = auth.start_flow(redirect_uri=_REDIRECT)
    b = auth.start_flow(redirect_uri=_REDIRECT)
    assert a.state != b.state
    assert a.code_verifier != b.code_verifier


# ---------------------------------------------------------------------------
# complete_flow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_flow_calls_token_endpoint_with_pkce(httpx_mock: HTTPXMock) -> None:
    """POST на token endpoint содержит все обязательные PKCE-поля и парсит Token."""
    auth = FamilySearchAuth(client_id="app-key")
    request = auth.start_flow(redirect_uri=_REDIRECT)

    httpx_mock.add_response(
        method="POST",
        url=auth.config.token_url,
        json={
            "access_token": "fs-access-123",
            "refresh_token": "fs-refresh-xyz",
            "expires_in": 3600,
            "scope": "openid",
            "token_type": "Bearer",
        },
        status_code=200,
    )

    async with httpx.AsyncClient() as client:
        token = await auth.complete_flow(
            code="auth-code-from-callback",
            request=request,
            redirect_uri=_REDIRECT,
            client=client,
        )

    assert token.access_token == "fs-access-123"
    assert token.refresh_token == "fs-refresh-xyz"
    assert token.expires_in == 3600
    assert token.scope == "openid"

    sent = httpx_mock.get_request()
    assert sent is not None
    body = parse_qs(sent.content.decode("utf-8"))
    assert body["grant_type"] == ["authorization_code"]
    assert body["code"] == ["auth-code-from-callback"]
    assert body["redirect_uri"] == [_REDIRECT]
    assert body["client_id"] == ["app-key"]
    assert body["code_verifier"] == [request.code_verifier]


@pytest.mark.asyncio
async def test_complete_flow_handles_invalid_grant_as_auth_error(httpx_mock: HTTPXMock) -> None:
    """400 + error=invalid_grant маппится в AuthError, не ClientError."""
    auth = FamilySearchAuth(client_id="app-key")
    request = auth.start_flow(redirect_uri=_REDIRECT)

    httpx_mock.add_response(
        method="POST",
        url=auth.config.token_url,
        json={
            "error": "invalid_grant",
            "error_description": "Authorization code expired or already used.",
        },
        status_code=400,
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(AuthError, match="invalid_grant"):
            await auth.complete_flow(
                code="bad-code",
                request=request,
                redirect_uri=_REDIRECT,
                client=client,
            )


@pytest.mark.asyncio
async def test_complete_flow_raises_auth_error_when_access_token_missing(
    httpx_mock: HTTPXMock,
) -> None:
    """Битый payload без access_token — AuthError, не silent."""
    auth = FamilySearchAuth(client_id="app-key")
    request = auth.start_flow(redirect_uri=_REDIRECT)

    httpx_mock.add_response(
        method="POST",
        url=auth.config.token_url,
        json={"expires_in": 3600},  # access_token отсутствует
        status_code=200,
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(AuthError, match="access_token"):
            await auth.complete_flow(
                code="x", request=request, redirect_uri=_REDIRECT, client=client
            )


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_new_token(httpx_mock: HTTPXMock) -> None:
    """refresh шлёт grant_type=refresh_token и парсит Token."""
    auth = FamilySearchAuth(client_id="app-key")

    httpx_mock.add_response(
        method="POST",
        url=auth.config.token_url,
        json={
            "access_token": "new-access-456",
            "refresh_token": "rotated-refresh",
            "expires_in": 3600,
            "scope": "openid",
        },
        status_code=200,
    )

    async with httpx.AsyncClient() as client:
        token = await auth.refresh(refresh_token="old-refresh", client=client)

    assert token.access_token == "new-access-456"
    assert token.refresh_token == "rotated-refresh"

    sent = httpx_mock.get_request()
    assert sent is not None
    body = parse_qs(sent.content.decode("utf-8"))
    assert body["grant_type"] == ["refresh_token"]
    assert body["refresh_token"] == ["old-refresh"]
    assert body["client_id"] == ["app-key"]


@pytest.mark.asyncio
async def test_refresh_invalid_refresh_token_raises_auth_error(httpx_mock: HTTPXMock) -> None:
    """Истёкший/отозванный refresh_token → AuthError."""
    auth = FamilySearchAuth(client_id="app-key")

    httpx_mock.add_response(
        method="POST",
        url=auth.config.token_url,
        json={"error": "invalid_grant", "error_description": "Refresh token revoked."},
        status_code=400,
    )

    async with httpx.AsyncClient() as client:
        with pytest.raises(AuthError, match="invalid_grant"):
            await auth.refresh(refresh_token="revoked", client=client)
