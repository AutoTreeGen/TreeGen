"""FamilySearch API client (Phase 5.0).

См. ADR-0011 для архитектурного обоснования и README для quickstart.
"""

from __future__ import annotations

from .auth import AuthorizationRequest, FamilySearchAuth, Token
from .client import FamilySearchClient
from .config import FamilySearchConfig
from .errors import (
    AuthError,
    ClientError,
    FamilySearchError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from .models import FsFact, FsGender, FsName, FsPerson, FsRelationship

__all__ = [
    "AuthError",
    "AuthorizationRequest",
    "ClientError",
    "FamilySearchAuth",
    "FamilySearchClient",
    "FamilySearchConfig",
    "FamilySearchError",
    "FsFact",
    "FsGender",
    "FsName",
    "FsPerson",
    "FsRelationship",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "Token",
]
