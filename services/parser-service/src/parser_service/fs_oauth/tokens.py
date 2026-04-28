"""Шифрование/дешифровка FamilySearch OAuth-токенов (Phase 5.1, ADR-0027).

Колонка ``users.fs_token_encrypted`` (text NULLABLE) хранит Fernet
ciphertext (URL-safe base64) от JSON-payload'а:

.. code-block:: json

    {
      "access_token": "eyJraWQi...",
      "refresh_token": "...",
      "expires_at": "2026-04-28T15:30:00+00:00",
      "scope": "openid profile",
      "fs_user_id": "MMMM-MMM",
      "stored_at": "2026-04-28T14:30:00+00:00"
    }

Ключ задаётся ENV ``PARSER_SERVICE_FS_TOKEN_KEY`` (32-байт base64url —
``Fernet.generate_key()`` output). Поддерживается ротация через
``MultiFernet`` (см. :class:`TokenStorage`).

Все ошибки шифрования/дешифровки нормализуются в
:class:`TokenCryptoError` — caller'у не приходится знать про
``cryptography.exceptions``.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class TokenCryptoError(RuntimeError):
    """Ошибка шифрования/дешифровки FamilySearch-токена.

    Ловится в caller'е, чтобы не упасть наружу с raw cryptography-ошибкой
    (из неё можно извлечь meta-информацию о формате ciphertext'а).
    """


@dataclass(frozen=True, kw_only=True, slots=True)
class FsStoredToken:
    """Расшифрованный OAuth-payload из ``users.fs_token_encrypted``.

    Attributes:
        access_token: Bearer-токен для FamilySearch API.
        refresh_token: Long-lived refresh-токен (90 days native app).
            ``None``, если FamilySearch его не вернул.
        expires_at: UTC-timestamp, после которого ``access_token``
            невалиден. Для proactive-refresh сравниваем с ``now + 60s``.
        scope: Скопы, которые реально были выданы.
        fs_user_id: FamilySearch user id (``current_user`` endpoint).
            Используется для traceability в provenance.
        stored_at: Когда мы записали этот payload (UTC).
    """

    access_token: str
    refresh_token: str | None
    expires_at: dt.datetime
    scope: str | None
    fs_user_id: str | None
    stored_at: dt.datetime

    def is_expired(self, *, now: dt.datetime | None = None, leeway_seconds: int = 60) -> bool:
        """True, если ``access_token`` протух (с запасом ``leeway_seconds``)."""
        moment = now or dt.datetime.now(dt.UTC)
        return moment + dt.timedelta(seconds=leeway_seconds) >= self.expires_at

    def to_payload(self) -> dict[str, Any]:
        """Сериализация в JSON-friendly dict (для шифрования / отладки)."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
            "scope": self.scope,
            "fs_user_id": self.fs_user_id,
            "stored_at": self.stored_at.isoformat(),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class TokenStorage:
    """Обёртка над Fernet/MultiFernet для шифровки/расшифровки токенов.

    Создаётся через :func:`get_token_storage` из ENV; в тестах — напрямую
    с фиксированным ключом.

    Attributes:
        fernet: Активный шифровальщик. ``MultiFernet`` поддерживает
            период ротации: новый ключ первым, старый вторым; при
            расшифровке пробует все, при шифровании использует первый.
    """

    fernet: Fernet | MultiFernet

    def encrypt(self, token: FsStoredToken) -> str:
        """Сериализовать payload и зашифровать в URL-safe base64 строку."""
        plaintext = json.dumps(token.to_payload(), separators=(",", ":")).encode("utf-8")
        try:
            ciphertext: bytes = self.fernet.encrypt(plaintext)
        except (TypeError, ValueError) as e:  # pragma: no cover — defensive
            msg = f"Failed to encrypt FamilySearch token payload: {e}"
            raise TokenCryptoError(msg) from e
        return ciphertext.decode("ascii")

    def decrypt(self, ciphertext: str) -> FsStoredToken:
        """Расшифровать строку из БД в :class:`FsStoredToken`.

        Raises:
            TokenCryptoError: Ciphertext повреждён, ключ не подходит, или
                payload невалиден (битый JSON / нет access_token).
        """
        try:
            raw = self.fernet.decrypt(ciphertext.encode("ascii"))
        except InvalidToken as e:
            msg = "FamilySearch token ciphertext is invalid or key mismatch"
            raise TokenCryptoError(msg) from e
        try:
            payload: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            msg = "FamilySearch token plaintext is not valid UTF-8 JSON"
            raise TokenCryptoError(msg) from e

        return _payload_to_token(payload)


def _payload_to_token(payload: dict[str, Any]) -> FsStoredToken:
    """Маппит JSON-payload в :class:`FsStoredToken` с валидацией."""
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        msg = "FamilySearch token payload missing access_token"
        raise TokenCryptoError(msg)
    refresh_token_raw = payload.get("refresh_token")
    refresh_token = refresh_token_raw if isinstance(refresh_token_raw, str) else None

    expires_at_raw = payload.get("expires_at")
    if not isinstance(expires_at_raw, str):
        msg = "FamilySearch token payload missing expires_at"
        raise TokenCryptoError(msg)
    try:
        expires_at = dt.datetime.fromisoformat(expires_at_raw)
    except ValueError as e:
        msg = f"FamilySearch token payload has invalid expires_at: {expires_at_raw!r}"
        raise TokenCryptoError(msg) from e

    stored_at_raw = payload.get("stored_at")
    if isinstance(stored_at_raw, str):
        try:
            stored_at = dt.datetime.fromisoformat(stored_at_raw)
        except ValueError:
            stored_at = dt.datetime.now(dt.UTC)
    else:
        stored_at = dt.datetime.now(dt.UTC)

    scope_raw = payload.get("scope")
    scope = scope_raw if isinstance(scope_raw, str) else None
    fs_user_id_raw = payload.get("fs_user_id")
    fs_user_id = fs_user_id_raw if isinstance(fs_user_id_raw, str) else None

    return FsStoredToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scope=scope,
        fs_user_id=fs_user_id,
        stored_at=stored_at,
    )


def encrypt_fs_token(storage: TokenStorage, token: FsStoredToken) -> str:
    """Module-level shortcut: ``storage.encrypt(token)``.

    Существует, чтобы тестам и call-site'ам не приходилось импортировать
    класс — type hint достаточный.
    """
    return storage.encrypt(token)


def decrypt_fs_token(storage: TokenStorage, ciphertext: str) -> FsStoredToken:
    """Module-level shortcut: ``storage.decrypt(ciphertext)``."""
    return storage.decrypt(ciphertext)


def is_fs_token_storage_configured(key_env: str) -> bool:
    """True, если в ENV задан валидный (по формату) Fernet-ключ.

    Используется HTTP-эндпоинтами, чтобы вернуть 503 «не настроено», а
    не 500 ImportError, если админ забыл выставить
    ``PARSER_SERVICE_FS_TOKEN_KEY``.
    """
    if not key_env:
        return False
    try:
        Fernet(key_env.encode("ascii"))
    except (ValueError, TypeError):
        return False
    return True


def get_token_storage(primary_key: str, *, fallback_keys: tuple[str, ...] = ()) -> TokenStorage:
    """Построить :class:`TokenStorage` из активного и (опционально) старых ключей.

    Args:
        primary_key: Активный Fernet-ключ (URL-safe base64 32 байта).
            Используется для шифрования; первый среди ключей при decrypt.
        fallback_keys: Старые ключи (для периода ротации). Применяются
            только при decrypt, не при encrypt.

    Raises:
        TokenCryptoError: Ключ не парсится как Fernet (битая длина / не
            base64). Сообщение специально без самого ключа в тексте.
    """
    if not primary_key:
        msg = "FS_TOKEN_KEY is empty"
        raise TokenCryptoError(msg)
    try:
        primary = Fernet(primary_key.encode("ascii"))
    except (ValueError, TypeError) as e:
        msg = "FS_TOKEN_KEY is not a valid Fernet key"
        raise TokenCryptoError(msg) from e

    if not fallback_keys:
        return TokenStorage(fernet=primary)

    fernets: list[Fernet] = [primary]
    for k in fallback_keys:
        if not k:
            continue
        try:
            fernets.append(Fernet(k.encode("ascii")))
        except (ValueError, TypeError) as e:
            msg = "FS_TOKEN_KEY fallback entry is not a valid Fernet key"
            raise TokenCryptoError(msg) from e
    return TokenStorage(fernet=MultiFernet(fernets))
