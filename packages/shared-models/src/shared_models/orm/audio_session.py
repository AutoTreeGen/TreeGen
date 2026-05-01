"""AudioSession — voice-to-tree session row (Phase 10.9a / ADR-0064).

Owner записывает аудио (WebM/Opus в браузере), backend кладёт blob в
storage и создаёт row, arq-worker дёргает Whisper и переключает status
``uploaded → transcribing → ready/failed``.

Service-table pattern: не наследует ``TreeEntityMixins`` и **не** наследует
``SoftDeleteMixin`` — иначе попадает под audit-listener (см.
``shared_models.audit._is_audited``), который аудитит как domain-факт с
``version_id`` и audit_log-row на каждое изменение. Это — артефакт AI-вызова,
не genealogy-edit. Hard-delete вместе с tree (FK CASCADE) или через ADR-0049
erasure pipeline.

``deleted_at`` объявлен напрямую — тот же семантический soft-delete, но без
audit-trigger'а (mirror DnaConsent.revoked_at и SourceExtraction подхода).

``consent_egress_at`` NOT NULL — критическая privacy-инварианта. Insert
без consent должен падать на DB-уровне; это последняя линия privacy-gate
поверх UI- и API-валидаторов (defence-in-depth, ADR-0064 §Риски).

``consent_egress_provider`` — String, *не* enum на DB-уровне. Phase 10.9.x
добавит ``self-hosted-whisper`` как privacy-tier опцию; не хочется
миграции ради нового допустимого значения. Pydantic-уровень (parser-service,
agent #10) применяет ``Literal[...]``.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, ProvenanceMixin, TimestampMixin


class AudioSessionStatus(enum.StrEnum):
    """Lifecycle ``audio_sessions.status``.

    - ``UPLOADED``: blob лежит в storage, ждёт worker'а.
    - ``TRANSCRIBING``: worker забрал, идёт STT-вызов.
    - ``READY``: транскрипт сохранён, видим в UI.
    - ``FAILED``: retry-budget исчерпан, см. ``error_message``.
    """

    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    READY = "ready"
    FAILED = "failed"


class AudioSession(IdMixin, ProvenanceMixin, TimestampMixin, Base):
    """Одна voice-to-tree сессия (audio + transcript).

    FK ``tree_id → trees.id ON DELETE CASCADE``: при удалении дерева
    (ADR-0049 erasure pipeline) audio-сессии чистятся вместе с blob'ами
    через application-level worker (см. ``erase_audio_session.py`` в #10).
    DB-CASCADE — safety net на случай прямого ``DELETE FROM trees``.

    FK ``owner_user_id → users.id ON DELETE RESTRICT``: пользователь не
    может быть удалён, пока у него остаются audio-сессии — иначе теряем
    audit-trail. GDPR erasure обнуляет аудио до user'а.
    """

    __tablename__ = "audio_sessions"
    __table_args__ = (
        # Список активных сессий в дереве + worker poll «найти всё, что не
        # удалено в этом дереве» — leftmost-prefix покрывает обе.
        Index(
            "ix_audio_sessions_tree_id_deleted_at",
            "tree_id",
            "deleted_at",
        ),
        # Worker-side: «все pending для retry» — частый и узкий запрос.
        Index("ix_audio_sessions_status", "status"),
        CheckConstraint(
            "status IN ('uploaded', 'transcribing', 'ready', 'failed')",
            name="ck_audio_sessions_status",
        ),
        CheckConstraint(
            "duration_sec IS NULL OR duration_sec >= 0",
            name="ck_audio_sessions_duration_nonneg",
        ),
        CheckConstraint(
            "size_bytes >= 0",
            name="ck_audio_sessions_size_nonneg",
        ),
        CheckConstraint(
            "transcript_cost_usd IS NULL OR transcript_cost_usd >= 0",
            name="ck_audio_sessions_transcript_cost_nonneg",
        ),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Storage
    storage_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Transcription
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=AudioSessionStatus.UPLOADED.value,
        server_default=AudioSessionStatus.UPLOADED.value,
    )
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transcript_cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=4),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Privacy gate (snapshot consent на момент записи) — NOT NULL критично.
    consent_egress_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consent_egress_provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )

    # Soft-delete: объявлен напрямую, **не** через ``SoftDeleteMixin``
    # — миксин включил бы audit-trigger (см. модуль docstring).
    deleted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )

    @property
    def is_deleted(self) -> bool:
        """Помечена ли сессия как удалённая (mirror ``SoftDeleteMixin``)."""
        return self.deleted_at is not None


__all__ = ["AudioSession", "AudioSessionStatus"]
