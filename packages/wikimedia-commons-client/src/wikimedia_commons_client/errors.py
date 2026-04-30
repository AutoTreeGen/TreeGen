"""Типизированные исключения клиента Wikimedia Commons.

Иерархия совпадает с :mod:`familysearch_client.errors` (ADR-0011),
чтобы caller'ы могли единообразно обрабатывать ошибки внешних API.
В отличие от FamilySearch, у Commons нет OAuth — поэтому нет
``AuthError``: 401/403 от Commons означает не «токен битый», а
«User-Agent недостаточно описательный» или «IP заблокирован», что
семантически ближе к ClientError (caller сам себе виноват, retry не
поможет).

    WikimediaCommonsError
    ├── NotFoundError            # 404 + пустые результаты, где caller ожидал
    ├── RateLimitError           # 429, несёт retry_after
    ├── ServerError              # 5xx, retryable
    └── ClientError              # 4xx прочие, non-retryable
"""

from __future__ import annotations


class WikimediaCommonsError(Exception):
    """Базовое исключение клиента Wikimedia Commons."""


class NotFoundError(WikimediaCommonsError):
    """Ресурс не найден (HTTP 404 или пустой результат там, где caller ожидает данные)."""


class RateLimitError(WikimediaCommonsError):
    """Сработал rate limit (HTTP 429).

    Args:
        message: Человекочитаемое описание.
        retry_after: Значение из заголовка ``Retry-After`` (секунды),
            если присутствует. ``None`` — заголовка не было.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(WikimediaCommonsError):
    """Серверная ошибка Wikimedia (HTTP 5xx). Retryable."""


class ClientError(WikimediaCommonsError):
    """Прочие 4xx — bad request, 401/403 (UA-policy violation), 422 etc. Non-retryable."""
