"""Wikimedia Commons API client (Phase 9.1).

См. ADR-0058 для архитектурного обоснования и README для quickstart.
"""

from __future__ import annotations

from .client import RetryPolicy, WikimediaCommonsClient
from .config import WikimediaCommonsConfig
from .errors import (
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
    WikimediaCommonsError,
)
from .models import Attribution, CommonsImage, License

__all__ = [
    "Attribution",
    "ClientError",
    "CommonsImage",
    "License",
    "NotFoundError",
    "RateLimitError",
    "RetryPolicy",
    "ServerError",
    "WikimediaCommonsClient",
    "WikimediaCommonsConfig",
    "WikimediaCommonsError",
]
