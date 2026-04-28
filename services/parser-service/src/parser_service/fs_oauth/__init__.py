"""FamilySearch OAuth helpers — token crypto + state store (Phase 5.1).

См. ADR-0027 для контекста: server-side OAuth flow с at-rest шифрованием
токенов в ``users.fs_token_encrypted`` (Fernet) и одноразовым state в
Redis с 10-минутным TTL.
"""

from __future__ import annotations

from .state_store import (
    OAuthStateRecord,
    consume_state,
    save_state,
)
from .tokens import (
    FsStoredToken,
    TokenCryptoError,
    TokenStorage,
    decrypt_fs_token,
    encrypt_fs_token,
    get_token_storage,
    is_fs_token_storage_configured,
)

__all__ = [
    "FsStoredToken",
    "OAuthStateRecord",
    "TokenCryptoError",
    "TokenStorage",
    "consume_state",
    "decrypt_fs_token",
    "encrypt_fs_token",
    "get_token_storage",
    "is_fs_token_storage_configured",
    "save_state",
]
