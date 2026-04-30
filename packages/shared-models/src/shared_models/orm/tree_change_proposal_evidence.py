"""TreeChangeProposalEvidence — source-citation для одного proposal (Phase 15.4 / ADR-0062).

Many-to-many между ``tree_change_proposals`` и ``sources`` с opaque
``relationship_ref`` jsonb (caller указывает, какой конкретный change
из proposal-diff support'ит этот источник; формат — opaque JSON,
парсится 15.4b approve-validator'ом).

Audit-trail, не tree-entity: без provenance / version_id / soft-delete.
В ``test_schema_invariants.SERVICE_TABLES``.

Constraint: один источник может быть прикреплён несколько раз с
разным ``relationship_ref`` (поддерживаем re-use одной и той же source
record для разных предлагаемых изменений), поэтому UNIQUE на тройку
``(proposal_id, source_id, relationship_ref)``, не двойку.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.mixins import IdMixin

if TYPE_CHECKING:
    from shared_models.orm.source import Source
    from shared_models.orm.tree_change_proposal import TreeChangeProposal
    from shared_models.orm.user import User


class TreeChangeProposalEvidence(IdMixin, Base):
    """Один источник, прикреплённый к одному proposal."""

    __tablename__ = "tree_change_proposal_evidence"
    __table_args__ = (
        Index(
            "ux_tree_change_proposal_evidence_unique",
            "proposal_id",
            "source_id",
            "relationship_ref",
            unique=True,
        ),
        Index(
            "ix_tree_change_proposal_evidence_proposal",
            "proposal_id",
        ),
    )

    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tree_change_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    citation: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Свободно-форменная цитата из источника, support'ящая change.",
    )
    relationship_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Opaque pointer на конкретный change в diff-е proposal'а. "
            "Формат — caller-defined ({entity_type, entity_id, kind, ...}); "
            "парсится approve-validator'ом (15.4b)."
        ),
    )
    added_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    added_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # ---- relationships -----------------------------------------------------
    proposal: Mapped[TreeChangeProposal] = relationship(
        "TreeChangeProposal",
        foreign_keys=[proposal_id],
        back_populates="evidence",
        lazy="raise",
    )
    source: Mapped[Source] = relationship(
        "Source",
        foreign_keys=[source_id],
        lazy="raise",
    )
    added_by: Mapped[User] = relationship(
        "User",
        foreign_keys=[added_by_user_id],
        lazy="raise",
    )


__all__ = ["TreeChangeProposalEvidence"]
