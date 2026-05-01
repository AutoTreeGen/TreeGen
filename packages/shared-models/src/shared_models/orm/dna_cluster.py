"""DnaCluster + DnaClusterMember (Phase 6.7a / ADR-0063).

Один cluster — это результат одного auto-clustering run'а на match-list
конкретного пользователя. ``user_id`` — Clerk user id (text), не FK на
internal users (DNA-сервис в проде может работать вне tree-scope).

Service-table'ы: нет soft-delete / provenance / version_id — re-run
clustering = новая row, старые остаются как audit/history.

Поля ``ai_label`` / ``ai_label_confidence`` заполняются Phase 6.7c
(AI labels), ``pile_up_score`` — Phase 6.7b. Текущий PR (6.7a) только
шипит таблицу + Leiden / endogamy; AI/pile-up колонки остаются
NULL до своих фаз.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.types import new_uuid


class DnaCluster(Base):
    """Один auto-clustering run.

    ``algorithm`` — 'leiden' | 'networkx_greedy' (fallback, см. ADR-0063).
    ``parameters`` — jsonb с настройками алгоритма (resolution, min_cm,
    threshold) — фиксируется для reproducibility.

    ``population_label`` — 'AJ' | 'mennonite' | 'iberian_sephardic' |
    None. Heuristic guess от endogamy detector'а; non-authoritative.
    Reference-panel-based detection — Phase 6.5+.
    """

    __tablename__ = "dna_clusters"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    algorithm: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    endogamy_warning: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    population_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pile_up_score: Mapped[float | None] = mapped_column(
        Numeric(precision=4, scale=3), nullable=True
    )
    ai_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ai_label_confidence: Mapped[float | None] = mapped_column(
        Numeric(precision=3, scale=2), nullable=True
    )
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class DnaClusterMember(Base):
    """Membership: одна row per (cluster, match) пара.

    ``membership_strength`` ∈ [0, 1] — у Leiden'а можно интерпретировать как
    долю пар-внутри-кластера, у NetworkX-greedy просто 1.0 (binary
    membership). Композитный PK предотвращает дубли; ON DELETE CASCADE —
    при удалении cluster или match очищаем связи.
    """

    __tablename__ = "dna_cluster_members"

    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_clusters.id", ondelete="CASCADE"),
        primary_key=True,
    )
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_matches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    membership_strength: Mapped[float | None] = mapped_column(
        Numeric(precision=4, scale=3), nullable=True
    )
