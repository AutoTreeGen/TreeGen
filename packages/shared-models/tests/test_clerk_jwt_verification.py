"""Unit-тесты verify_clerk_jwt (Phase 4.10, ADR-0033).

Никаких реальных HTTP-вызовов в Clerk — генерируем in-memory RSA-pair,
строим minimal JWKS-документ, подменяем module-level кэш через
``reset_jwks_cache`` + кастомный :class:`JwksCache`-double.

Покрывает:

* happy path: valid signed JWT → ClerkClaims с правильными sub/email.
* expired JWT → AuthError.
* wrong issuer → AuthError.
* tampered signature → AuthError.
* отсутствующий sub → AuthError.

Pure-Python тесты, не требуют DB или сети.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from shared_models.auth import (
    AuthError,
    ClerkJwtSettings,
    JwksCache,
    verify_clerk_jwt,
)
from shared_models.auth.clerk_jwt import reset_jwks_cache

# ---------------------------------------------------------------------------
# Test fixtures: в-памяти RSA-pair + fake JwksCache
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Сгенерировать одну RSA-пару на модуль (генерация — медленная)."""
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def _sign_jwt(
    private_key: rsa.RSAPrivateKey,
    payload: dict[str, Any],
    *,
    kid: str = "test-kid",
) -> str:
    """Подписать payload RS256 с указанным kid в header'е."""
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


class _FakeJwksCache(JwksCache):
    """Подменённый кэш: возвращает single signing key из in-memory pair."""

    def __init__(self, public_key: rsa.RSAPublicKey) -> None:
        # Не зовём super().__init__ — нам не нужны JWKS-fetcher и lock.
        self._public_key = public_key

    async def get_signing_key(self, token: str) -> Any:  # noqa: ARG002
        # Возвращаем simple namespace-like объект с .key — verify_clerk_jwt
        # ожидает ``signing_key.key`` для ``jwt.decode``.
        class _SigningKey:
            def __init__(self, key: rsa.RSAPublicKey) -> None:
                self.key = key

        return _SigningKey(self._public_key)


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    """Сбрасывать module-level JWKS-кэш между тестами."""
    reset_jwks_cache()


@pytest.fixture
def settings() -> ClerkJwtSettings:
    return ClerkJwtSettings(
        issuer="https://test.clerk.dev",
        jwks_url="https://test.clerk.dev/.well-known/jwks.json",
        leeway_seconds=0,
    )


def _claims(
    *,
    sub: str = "user_123",
    email: str | None = "user@example.com",
    iss: str = "https://test.clerk.dev",
    exp_offset: int = 3600,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal Clerk-style claims dict."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": iss,
        "sub": sub,
        "iat": now,
        "exp": now + exp_offset,
    }
    if email is not None:
        payload["email"] = email
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_clerk_jwt_returns_claims_for_valid_token(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    private, public = rsa_keypair
    token = _sign_jwt(private, _claims(sub="user_alpha", email="alpha@example.com"))
    cache = _FakeJwksCache(public)

    claims = await verify_clerk_jwt(token, settings, jwks_cache=cache)

    assert claims.sub == "user_alpha"
    assert claims.email == "alpha@example.com"
    # Raw содержит весь payload, в т.ч. iat/exp.
    assert claims.raw["sub"] == "user_alpha"
    assert "exp" in claims.raw


@pytest.mark.asyncio
async def test_verify_clerk_jwt_rejects_expired_token(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    private, public = rsa_keypair
    token = _sign_jwt(private, _claims(exp_offset=-60))  # expired 60s ago
    cache = _FakeJwksCache(public)

    with pytest.raises(AuthError):
        await verify_clerk_jwt(token, settings, jwks_cache=cache)


@pytest.mark.asyncio
async def test_verify_clerk_jwt_rejects_wrong_issuer(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    private, public = rsa_keypair
    token = _sign_jwt(private, _claims(iss="https://attacker.example.com"))
    cache = _FakeJwksCache(public)

    with pytest.raises(AuthError):
        await verify_clerk_jwt(token, settings, jwks_cache=cache)


@pytest.mark.asyncio
async def test_verify_clerk_jwt_rejects_tampered_signature(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    private, public = rsa_keypair
    token = _sign_jwt(private, _claims())
    # Tamper: меняем символ ВНУТРИ signature-части (после второй '.'),
    # причём не на close-by по base64url алфавиту, чтобы guarantee'ить
    # invalid signature. Изменение только последнего символа было pre-
    # existing brittleness — в зависимости от alphabet position иногда
    # base64-padding compensate'ил изменение и подпись все ещё парсилась.
    last_dot = token.rfind(".")
    sig_part = token[last_dot + 1 :]
    mid = len(sig_part) // 2
    new_char = "Z" if sig_part[mid] != "Z" else "a"
    tampered = token[: last_dot + 1] + sig_part[:mid] + new_char + sig_part[mid + 1 :]
    cache = _FakeJwksCache(public)

    with pytest.raises(AuthError):
        await verify_clerk_jwt(tampered, settings, jwks_cache=cache)


@pytest.mark.asyncio
async def test_verify_clerk_jwt_requires_sub_claim(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    private, public = rsa_keypair
    payload = _claims()
    payload.pop("sub")
    token = _sign_jwt(private, payload)
    cache = _FakeJwksCache(public)

    with pytest.raises(AuthError):
        await verify_clerk_jwt(token, settings, jwks_cache=cache)


@pytest.mark.asyncio
async def test_verify_clerk_jwt_handles_token_without_email(
    rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey],
    settings: ClerkJwtSettings,
) -> None:
    """Frontend tokens Clerk не включают email; ``email`` должен быть None."""
    private, public = rsa_keypair
    token = _sign_jwt(private, _claims(sub="user_no_email", email=None))
    cache = _FakeJwksCache(public)

    claims = await verify_clerk_jwt(token, settings, jwks_cache=cache)
    assert claims.sub == "user_no_email"
    assert claims.email is None


@pytest.mark.asyncio
async def test_empty_token_raises_auth_error(settings: ClerkJwtSettings) -> None:
    with pytest.raises(AuthError):
        await verify_clerk_jwt("", settings)


# Помечаем тест как не требующий БД, чтобы он шёл в быстром цикле.
pytestmark = pytest.mark.asyncio
# `dt` импортирован для возможного будущего расширения тестов с
# абсолютными timestamp'ами; pyright/ruff не должны помечать как unused —
# использование внутри _claims через time.time() даёт seconds, не datetime.
_ = dt
