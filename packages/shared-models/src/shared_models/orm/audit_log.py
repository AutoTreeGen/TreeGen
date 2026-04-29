"""AuditLog — журнал всех изменений доменных записей дерева.

См. ADR-0003. Запись производится через SQLAlchemy event listener
``register_audit_listeners`` (см. ``shared_models.audit``) для tree-scoped
сущностей (persons, families, events, places, sources, ...).

Phase 4.11a (миграция 0021, ADR-0046): добавлена поддержка user-level
audit-entry'ев для GDPR-action'ов (export request / processing /
completed / failed, erasure_requested). Такие записи имеют
``tree_id IS NULL``, ``entity_type='user_action_request'``,
``entity_id = user_action_requests.id``, ``actor_user_id = user_id``.
Auto-listener их не пишет (он отфильтровывает объекты без ``tree_id``)
— GDPR-action audit'ы вставляются вручную из
``parser_service.services.user_export_runner``. ``action`` расширен с
``varchar(16)`` до ``varchar(32)``, чтобы вместить
``"export_processing"`` и ``"erasure_requested"`` (17 символов).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.enums import ActorKind
from shared_models.mixins import IdMixin


class AuditLog(IdMixin, Base):
    """Запись об одном изменении одной сущности.

    Не наследует SoftDeleteMixin: audit-записи иммутабельны.
    Hard delete только через GDPR-flow (анонимизация PII в diff).

    ``tree_id`` опциональна с Phase 4.11a — для GDPR-action'ов user-уровня,
    которые не привязаны к конкретному дереву (см. ADR-0046).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tree_created", "tree_id", "created_at"),
        Index("ix_audit_log_entity_created", "entity_type", "entity_id", "created_at"),
        # Phase 4.11a: lookup «GDPR-actions данного user'а». Партиал — не
        # раздуваем индекс tree-scoped audit-записями, которых на порядки больше.
        Index(
            "ix_audit_log_user_actions",
            "actor_user_id",
            "action",
            "created_at",
            postgresql_where=text("tree_id IS NULL"),
        ),
    )

    tree_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=True,
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
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
