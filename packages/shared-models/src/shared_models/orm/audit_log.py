"""AuditLog — журнал всех изменений доменных записей дерева.

См. ADR-0003. Запись производится через SQLAlchemy event listener
``register_audit_listeners`` (см. ``shared_models.audit``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import ActorKind
from shared_models.mixins import IdMixin


class AuditLog(IdMixin, Base):
    """Запись об одном изменении одной сущности.

    Не наследует SoftDeleteMixin: audit-записи иммутабельны.
    Hard delete только через GDPR-flow (анонимизация PII в diff).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tree_created", "tree_id", "created_at"),
        Index("ix_audit_log_entity_created", "entity_type", "entity_id", "created_at"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ActorKind.SYSTEM.value,
        server_default=ActorKind.SYSTEM.value,
    )
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("import_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
