"""shared-models — общие модели AutoTreeGen.

Публичный API (минимальный, на старте):

- ``Base``                — DeclarativeBase для всех ORM-моделей.
- ``orm``                 — модуль с моделями.
- ``schemas``             — модуль с Pydantic DTO.
- ``EntityStatus``        — enum статуса доменных записей.
- ``register_audit_listeners`` — вешает event listeners на сессию.
"""

from __future__ import annotations

from shared_models.audit import is_audit_skipped, register_audit_listeners, set_audit_skip
from shared_models.base import Base
from shared_models.enums import (
    ActorKind,
    AuditAction,
    CollaboratorRole,
    EntityStatus,
    EventType,
    NameType,
    RelationType,
    Sex,
    SourceType,
    TreeVisibility,
)
from shared_models.observability import (
    CloudLoggingJSONFormatter,
    configure_json_logging,
    init_sentry,
)

__version__ = "0.1.0"

__all__ = [
    "ActorKind",
    "AuditAction",
    "Base",
    "CloudLoggingJSONFormatter",
    "CollaboratorRole",
    "EntityStatus",
    "EventType",
    "NameType",
    "RelationType",
    "Sex",
    "SourceType",
    "TreeVisibility",
    "__version__",
    "configure_json_logging",
    "init_sentry",
    "is_audit_skipped",
    "register_audit_listeners",
    "set_audit_skip",
]
