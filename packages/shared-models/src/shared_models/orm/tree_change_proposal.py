"""TreeChangeProposal — PR-style change request над деревом (Phase 15.4 / ADR-0062).

Audit/workflow log, не tree-entity:

* Без ``provenance`` / ``version_id`` / ``status``-mixin / ``confidence_score``
  / ``deleted_at``. Жизненный цикл — explicit state machine
  (``open → approved/rejected → merged → rolled_back``) в собственной
  ``status``-колонке с CHECK constraint.
* Соответственно — в ``test_schema_invariants.SERVICE_TABLES`` (не
  ``TREE_ENTITY_TABLES``).

``author_user_id`` / ``reviewed_by_user_id`` / ``rolled_back_by_user_id``
— UUID FK на ``users.id`` (CASCADE / SET NULL по policy GDPR-erasure;
см. ADR-0062 §«FK strategy»). Намеренно НЕ Clerk-id text: сохраняем
identity-целостность и cascade при удалении user'а.

``diff`` jsonb — структурированный representation предлагаемого
изменения (``{creates: [...], updates: [...], deletes: [...]}`` per
entity type). Schema валидируется на API-уровне (Pydantic) при
``POST /proposals``; здесь — opaque jsonb (15.4c merge engine
интерпретирует).

``merge_commit_id`` — указатель на ``audit_log`` row, созданный при
успешном merge (15.4c). NULL для proposals в состояниях
``open/approved/rejected``. ``ondelete="SET NULL"`` потому что
``audit_log`` может purge'аться отдельной retention-политикой, а
proposal-row остаётся как исторический факт.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.mixins import IdMixin

if TYPE_CHECKING:
    from shared_models.orm.audit_log import AuditLog
    from shared_models.orm.tree import Tree
    from shared_models.orm.tree_change_proposal_evidence import (
        TreeChangeProposalEvidence,
    )
    from shared_models.orm.user import User


class TreeChangeProposal(IdMixin, Base):
    """Один change request над деревом.

    State machine:

    * ``open`` — author создал, ждёт review (можно правка автором).
    * ``approved`` — reviewer одобрил, owner ещё не merged.
    * ``rejected`` — reviewer отклонил (terminal).
    * ``merged`` — owner применил diff к дереву; ``merge_commit_id``
      указывает на audit_log entry с deltas.
    * ``rolled_back`` — owner откатил merged proposal; ``rolled_back_at``
      / ``rolled_back_by_user_id`` заполнены.

    Constraint: status ∈ {open, approved, rejected, merged, rolled_back}
    (см. CHECK constraint).
    """

    __tablename__ = "tree_change_proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','approved','rejected','merged','rolled_back')",
            name="ck_tree_change_proposals_status",
        ),
        Index("ix_tree_change_proposals_tree_status", "tree_id", "status"),
        Index("ix_tree_change_proposals_author", "author_user_id"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment=(
            "Кто создал proposal. CASCADE on delete (GDPR-erasure): при "
            "hard-delete user'а его proposals удаляются вместе с ним; "
            "merged proposals остаются в audit_log."
        ),
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Структурированный diff: ``{creates: [...], updates: [...], "
            "deletes: [...]}`` per entity type. Pydantic-валидация на "
            "POST. Merge engine (Phase 15.4c) интерпретирует."
        ),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="open",
        server_default="open",
    )
    evidence_required: Mapped[list[Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
        comment=(
            "Auto-populated из ``tree.protection_policy.require_evidence_for`` "
            "при POST. Список ``{relationship_id, kind}`` — каждый item "
            "должен быть покрыт ``tree_change_proposal_evidence``-row "
            "перед approve (15.4b validation)."
        ),
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    merged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    merge_commit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("audit_log.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "Pointer на audit_log row, созданный при atomic-merge (15.4c). "
            "SET NULL on delete: audit_log retention purges не должны "
            "ломать proposal history."
        ),
    )
    rolled_back_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rolled_back_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---- relationships -----------------------------------------------------
    tree: Mapped[Tree] = relationship(
        "Tree",
        foreign_keys=[tree_id],
        lazy="raise",
    )
    author: Mapped[User] = relationship(
        "User",
        foreign_keys=[author_user_id],
        lazy="raise",
    )
    reviewer: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[reviewed_by_user_id],
        lazy="raise",
    )
    rolled_back_by: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[rolled_back_by_user_id],
        lazy="raise",
    )
    merge_commit: Mapped[AuditLog | None] = relationship(
        "AuditLog",
        foreign_keys=[merge_commit_id],
        lazy="raise",
    )
    evidence: Mapped[list[TreeChangeProposalEvidence]] = relationship(
        "TreeChangeProposalEvidence",
        back_populates="proposal",
        cascade="all, delete-orphan",
        lazy="raise",
    )


__all__ = ["TreeChangeProposal"]
