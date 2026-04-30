"""dna_clusters + dna_cluster_members + dna_pile_up_regions (Phase 6.7a / ADR-0063).

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-01

Создаёт три служебные таблицы для DNA AutoCluster + endogamy + pile-up
features. Phase 6.7a (этот PR) шипит **только** schema + Leiden /
endogamy detector в `dna-analysis`. Колонки ``ai_label`` /
``ai_label_confidence`` (заполняет 6.7c) и ``pile_up_score``
(заполняет 6.7b) присутствуют в schema сразу — последующие фазы только
write-path'ом расширяются, без новых миграций.

Не TreeEntity: служебные таблицы, без soft-delete / provenance /
version_id (см. ADR-0063 §«Persistence shape»).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create dna_clusters + dna_cluster_members + dna_pile_up_regions."""
    op.create_table(
        "dna_clusters",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("algorithm", sa.String(32), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "endogamy_warning",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("population_label", sa.String(32), nullable=True),
        sa.Column(
            "pile_up_score",
            sa.Numeric(precision=4, scale=3),
            nullable=True,
        ),
        sa.Column("ai_label", sa.String(128), nullable=True),
        sa.Column(
            "ai_label_confidence",
            sa.Numeric(precision=3, scale=2),
            nullable=True,
        ),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "algorithm IN ('leiden', 'networkx_greedy')",
            name="ck_dna_clusters_algorithm",
        ),
        sa.CheckConstraint(
            "pile_up_score IS NULL OR (pile_up_score >= 0 AND pile_up_score <= 1)",
            name="ck_dna_clusters_pile_up_score_range",
        ),
        sa.CheckConstraint(
            "ai_label_confidence IS NULL OR "
            "(ai_label_confidence >= 0 AND ai_label_confidence <= 1)",
            name="ck_dna_clusters_ai_label_confidence_range",
        ),
        sa.CheckConstraint("member_count >= 0", name="ck_dna_clusters_member_count_nonneg"),
    )
    op.create_index("ix_dna_clusters_user_id", "dna_clusters", ["user_id"])
    op.create_index(
        "ix_dna_clusters_user_created",
        "dna_clusters",
        ["user_id", "created_at"],
    )

    op.create_table(
        "dna_cluster_members",
        sa.Column(
            "cluster_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dna_clusters.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "match_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dna_matches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "membership_strength",
            sa.Numeric(precision=4, scale=3),
            nullable=True,
        ),
        sa.CheckConstraint(
            "membership_strength IS NULL OR "
            "(membership_strength >= 0 AND membership_strength <= 1)",
            name="ck_dna_cluster_members_strength_range",
        ),
    )
    # match_id отдельный индекс пригодится для «найти все кластеры данного match».
    # cluster_id уже покрыт композитным PK (cluster_id, match_id) — left-most.
    op.create_index(
        "ix_dna_cluster_members_match_id",
        "dna_cluster_members",
        ["match_id"],
    )

    op.create_table(
        "dna_pile_up_regions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("chromosome", sa.Integer(), nullable=False),
        sa.Column("start_position", sa.BigInteger(), nullable=False),
        sa.Column("end_position", sa.BigInteger(), nullable=False),
        sa.Column("population_label", sa.String(32), nullable=False),
        sa.Column(
            "coverage_pct",
            sa.Numeric(precision=5, scale=2),
            nullable=True,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "chromosome >= 1 AND chromosome <= 22",
            name="ck_dna_pile_up_regions_chromosome_range",
        ),
        sa.CheckConstraint(
            "end_position > start_position",
            name="ck_dna_pile_up_regions_interval",
        ),
        sa.CheckConstraint(
            "coverage_pct IS NULL OR (coverage_pct >= 0 AND coverage_pct <= 100)",
            name="ck_dna_pile_up_regions_coverage_range",
        ),
    )
    op.create_index(
        "ix_dna_pile_up_regions_chromosome",
        "dna_pile_up_regions",
        ["chromosome"],
    )
    op.create_index(
        "ix_dna_pile_up_regions_population_label",
        "dna_pile_up_regions",
        ["population_label"],
    )
    op.create_index(
        "ix_dna_pile_up_regions_pop_chrom",
        "dna_pile_up_regions",
        ["population_label", "chromosome"],
    )


def downgrade() -> None:
    """Drop dna_pile_up_regions + dna_cluster_members + dna_clusters."""
    op.drop_index(
        "ix_dna_pile_up_regions_pop_chrom",
        table_name="dna_pile_up_regions",
    )
    op.drop_index(
        "ix_dna_pile_up_regions_population_label",
        table_name="dna_pile_up_regions",
    )
    op.drop_index(
        "ix_dna_pile_up_regions_chromosome",
        table_name="dna_pile_up_regions",
    )
    op.drop_table("dna_pile_up_regions")

    op.drop_index(
        "ix_dna_cluster_members_match_id",
        table_name="dna_cluster_members",
    )
    op.drop_table("dna_cluster_members")

    op.drop_index("ix_dna_clusters_user_created", table_name="dna_clusters")
    op.drop_index("ix_dna_clusters_user_id", table_name="dna_clusters")
    op.drop_table("dna_clusters")
