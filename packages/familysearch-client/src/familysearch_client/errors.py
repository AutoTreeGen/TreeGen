"""Типизированные исключения клиента FamilySearch.

Иерархия (см. ADR-0011 §«Errors — типизированная иерархия»):

    FamilySearchError
    ├── AuthError                # 401, 403, OAuth-проблемы
    ├── NotFoundError            # 404
    ├── RateLimitError           # 429, несёт retry_after
    ├── ServerError              # 5xx, retryable
    └── ClientError              # 4xx прочие, non-retryable

Caller'у не нужно зависеть от ``httpx.HTTPStatusError`` — все ошибки
маппятся в эти классы в ``client.py``.
"""

from __future__ import annotations


class FamilySearchError(Exception):
    """Базовое исключение клиента FamilySearch."""


class AuthError(FamilySearchError):
    """Ошибка OAuth / авторизации (HTTP 401, 403, либо invalid_grant)."""


class NotFoundError(FamilySearchError):
    """Ресурс не найден (HTTP 404)."""


class RateLimitError(FamilySearchError):
    """Сработал rate limit (HTTP 429).

    Args:
        message: Человекочитаемое описание.
        retry_after: Значение из заголовка ``Retry-After`` (секунды),
            если присутствует. ``None`` — заголовка не было.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(FamilySearchError):
    """Серверная ошибка FamilySearch (HTTP 5xx). Retryable."""


class ClientError(FamilySearchError):
    """Прочие 4xx — bad request, conflict, unprocessable. Non-retryable."""
