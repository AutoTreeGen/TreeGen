"""Merge sessions / decisions / apply batches — Phase 5.7c-a (ADR-0070).

Сессионный 2-way merge: пользователь приходит на пару источников
(``left_ref``, ``right_ref``), резолвит per-field decisions, применяет
batched apply. Источники — гетерогенные (``imported_doc`` / ``tree`` /
``snapshot``), поэтому refs **полиморфные**: ``*_ref_kind`` enum +
``*_ref_id`` UUID без FK-constraint'а.

Service-table pattern (как ``audio_sessions`` / ``chat_sessions``): эти
таблицы — orchestration-artifact merge-service'а, не доменные записи
дерева. Поэтому:

* НЕ наследуют ``TreeEntityMixins`` — нет ``status``/``confidence_score``
  в ADR-0003 смысле; ``MergeSession.status`` — отдельный narrow lifecycle
  (pending → in_progress → ... → applied / abandoned).
* НЕ наследуют ``SoftDeleteMixin`` — ``abandoned`` status и так
  семантический tombstone сессии; и `SoftDeleteMixin` подключил бы
  audit-listener (см. ``shared_models.audit._is_audited``), который
  ожидает domain-факты с ``version_id``.
* НЕ наследуют ``ProvenanceMixin`` — provenance writes идут на затронутые
  domain-row'ы при apply (``provenance.merge_session_id`` + ``...decision_ids``,
  ADR-0070 §«Аудит и provenance»), а не на сами session-row'ы.

Регистрируются в ``SERVICE_TABLES`` (``test_schema_invariants.py``) —
иначе ``test_no_unexpected_tables`` падает (memory-note ``feedback_orm_allowlist.md``).

Phase 5.7c-a — schema only. merge-engine extraction (ADR-0070 §«Что нужно
сделать в коде» step 1) и сам ``services/merge-service`` scaffold (step 3) —
последующие PR'ы.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class MergeRefKind(enum.StrEnum):
    """Тип источника в polymorphic ref'е (``left_ref_kind`` / ``right_ref_kind``).

    - ``IMPORTED_DOC``: ImportJob payload (GEDCOM / FS pedigree / DNA).
    - ``TREE``: существующее дерево (``trees.id``).
    - ``SNAPSHOT``: версия дерева на момент времени. **Зарезервировано**
      на Phase 11+ (ADR-0070 §«Когда пересмотреть» — snapshot diff).
      Phase 7 запрещает на API-уровне; в БД присутствует, чтобы
      добавление позже не требовало миграции enum'а.
    """

    IMPORTED_DOC = "imported_doc"
    TREE = "tree"
    SNAPSHOT = "snapshot"


class MergeSessionStatus(enum.StrEnum):
    """Lifecycle ``merge_sessions.status``.

    Состояния — линейные с одной ветвью (``partially_applied`` →
    ``in_progress`` если пользователь добавил новые decisions, или →
    ``applied`` если оставшиеся skipped):

    - ``PENDING``: создана, scoring/diff ещё не пробежал.
    - ``IN_PROGRESS``: есть хотя бы один resolved/skipped decision,
      есть ещё unresolved.
    - ``READY_TO_APPLY``: все decisions либо resolved либо skipped, ни
      один батч ещё не применён.
    - ``PARTIALLY_APPLIED``: хотя бы один ``MergeApplyBatch`` зафиксирован,
      но в session ещё есть unresolved decisions.
    - ``APPLIED``: все decisions либо applied либо skipped — терминальное.
    - ``ABANDONED``: пользователь явно отменил — терминальное; row
      сохраняется для audit'а.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    READY_TO_APPLY = "ready_to_apply"
    PARTIALLY_APPLIED = "partially_applied"
    APPLIED = "applied"
    ABANDONED = "abandoned"


class MergeDecisionScope(enum.StrEnum):
    """На каком уровне сделано решение (``merge_decisions.scope``).

    - ``PERSON``: решение про целого человека (типично — skip / take-all-from-side).
    - ``RELATION``: решение про связь (parent-child, spouse).
    - ``SOURCE``: решение про привязку к Source.
    - ``MEDIA``: решение про MultimediaObject.
    """

    PERSON = "person"
    RELATION = "relation"
    SOURCE = "source"
    MEDIA = "media"


class ChosenSource(enum.StrEnum):
    """Какую сторону выбрал пользователь (``merge_decisions.chosen_source``).

    - ``LEFT`` / ``RIGHT``: значение из соответствующей стороны.
    - ``BOTH``: сохранить оба (типично для имён/транслитераций).
    - ``CUSTOM``: пользователь ввёл custom-значение в ``custom_value``.
    - ``SKIP``: отложить — не применять ни сейчас, ни в auto-resolve.
    """

    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"
    CUSTOM = "custom"
    SKIP = "skip"


class DecisionMethod(enum.StrEnum):
    """Как принято решение (``merge_decisions.decision_method``).

    - ``MANUAL``: пользователь кликнул radio.
    - ``AUTO``: bulk «Auto-resolve non-conflicts» — значения совпали
      побайтово после нормализации.
    - ``RULE``: сработало именованное правило (имя — в ``rule_id``).
      Каталог правил — отдельный YAML в merge-service'е (Phase 7+).
    """

    MANUAL = "manual"
    AUTO = "auto"
    RULE = "rule"


class MergeSession(IdMixin, TimestampMixin, Base):
    """Один сессионный 2-way merge run пользователя.

    Архитектура — ADR-0070. Сессия живёт долго (часы→дни), пользователь
    приходит и уходит, состояние и ``last_active_at`` персистируется.

    FK ``user_id → users.id ON DELETE RESTRICT`` — нельзя удалить юзера,
    пока у него остаются merge-сессии (audit-trail). GDPR erasure
    (ADR-0049) обнуляет сессии до user'а.

    FK ``target_tree_id → trees.id ON DELETE CASCADE`` — если целевое
    дерево удалено, сессия теряет смысл; CASCADE = safety net.
    Nullable: для случая ``imported_doc + imported_doc → new tree``
    target создаётся только на «Apply first batch» (ADR-0070
    §«Семантика target_tree_id»), иначе abandoned-сессии плодят пустые
    деревья.

    ``left_ref_id`` / ``right_ref_id`` **без** FK-constraint'а —
    полиморфизм через ``*_ref_kind``. Application-level валидация
    (resolve_ref в merge-service) обязательна; integration-тесты
    проверяют «kind=tree, id ∉ trees» → 400 (ADR-0070 §Axis 2 γ).
    """

    __tablename__ = "merge_sessions"
    __table_args__ = (
        # User'ские list-views: «мои активные merge-сессии». Самый
        # частый запрос (UI-deshboard), purchase-leftmost-prefix.
        Index(
            "ix_merge_sessions_user_status_last_active",
            "user_id",
            "status",
            "last_active_at",
        ),
        # Обратный lookup «есть ли уже сессия на эту пару refs?» —
        # дедуп при создании. Полиморфные колонки в одном индексе.
        Index(
            "ix_merge_sessions_left_ref",
            "left_ref_kind",
            "left_ref_id",
        ),
        Index(
            "ix_merge_sessions_right_ref",
            "right_ref_kind",
            "right_ref_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'ready_to_apply', "
            "'partially_applied', 'applied', 'abandoned')",
            name="ck_merge_sessions_status",
        ),
        CheckConstraint(
            "left_ref_kind IN ('imported_doc', 'tree', 'snapshot')",
            name="ck_merge_sessions_left_ref_kind",
        ),
        CheckConstraint(
            "right_ref_kind IN ('imported_doc', 'tree', 'snapshot')",
            name="ck_merge_sessions_right_ref_kind",
        ),
        # Phase 7 запрет: snapshot не используется. Когда Phase 11+
        # реализует snapshot diff, этот CHECK снимается отдельной
        # миграцией (ADR-0070 §«Когда пересмотреть»).
        CheckConstraint(
            "left_ref_kind <> 'snapshot' AND right_ref_kind <> 'snapshot'",
            name="ck_merge_sessions_no_snapshot_phase7",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_tree_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Polymorphic refs — see module docstring.
    left_ref_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    left_ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    right_ref_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    right_ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default=MergeSessionStatus.PENDING.value,
        server_default=MergeSessionStatus.PENDING.value,
    )
    # Lazy-derived snapshot: counts (resolved/pending/skipped), heuristics,
    # последнее apply_batch_id и т. п. UI читает отсюда без агрегатных
    # запросов. Обновляется application-side при decision/apply hooks.
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    last_active_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class MergeDecision(IdMixin, TimestampMixin, Base):
    """Одно решение (per-field или per-entity) внутри ``MergeSession``.

    Per-decision granularity — расширение ADR-0044 («majority → survivor»
    был known-limitation single-pair API). В session-flow каждое поле
    хранится отдельной row'ой, явно applied или skipped.

    FK ``session_id → merge_sessions.id ON DELETE CASCADE`` — decisions
    привязаны к сессии и удаляются с ней (но в практике сессии не
    удаляются; ``abandoned`` — статус, не tombstone).

    FK ``applied_in_batch_id → merge_apply_batches.id ON DELETE SET NULL`` —
    если batch когда-нибудь удалили (не предусмотрено, но safety net),
    decision сохраняется как «решение есть, но apply откатили».

    ``target_id`` — без FK-constraint'а: ``target_kind`` ∈
    {``person``, ``family``, ``event``, ...} полиморфен.

    ``rule_id`` — имя правила, если ``decision_method == 'rule'`` (например
    ``place_canonicalization``); иначе NULL. Каталог правил — YAML в
    merge-service (Phase 7+, ADR-0070 §«Аудит и provenance»).
    """

    __tablename__ = "merge_decisions"
    __table_args__ = (
        # Самый частый запрос — «все decisions сессии», + sorted UI-load.
        Index(
            "ix_merge_decisions_session_id_decided_at",
            "session_id",
            "decided_at",
        ),
        # Поиск: «уже было решение по этому объекту в сессии?» — дедуп
        # при auto-resolve.
        Index(
            "ix_merge_decisions_session_target",
            "session_id",
            "target_kind",
            "target_id",
        ),
        # Reverse: «какие decisions попали в этот batch?» — для undo
        # и audit-views.
        Index(
            "ix_merge_decisions_applied_in_batch",
            "applied_in_batch_id",
        ),
        CheckConstraint(
            "scope IN ('person', 'relation', 'source', 'media')",
            name="ck_merge_decisions_scope",
        ),
        CheckConstraint(
            "chosen_source IN ('left', 'right', 'both', 'custom', 'skip')",
            name="ck_merge_decisions_chosen_source",
        ),
        CheckConstraint(
            "decision_method IN ('manual', 'auto', 'rule')",
            name="ck_merge_decisions_decision_method",
        ),
        # Если method='rule', rule_id обязан быть указан; если другой
        # method — rule_id NULL.
        CheckConstraint(
            "(decision_method = 'rule' AND rule_id IS NOT NULL) "
            "OR (decision_method <> 'rule' AND rule_id IS NULL)",
            name="ck_merge_decisions_rule_id_consistency",
        ),
        # custom_value заполнен ⇔ chosen_source='custom'.
        CheckConstraint(
            "(chosen_source = 'custom' AND custom_value IS NOT NULL) "
            "OR (chosen_source <> 'custom' AND custom_value IS NULL)",
            name="ck_merge_decisions_custom_value_consistency",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Например ``person.birth.date``; для scope=person пустая строка.
    field_path: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    chosen_source: Mapped[str] = mapped_column(String(8), nullable=False)
    custom_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    decision_method: Mapped[str] = mapped_column(String(8), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    applied_in_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merge_apply_batches.id", ondelete="SET NULL"),
        nullable=True,
    )


class MergeApplyBatch(IdMixin, TimestampMixin, Base):
    """Один батч применения decisions из ``MergeSession``.

    «Apply ready 38» = одна row здесь + 38 ``MergeDecision``-row'ов
    с ``applied_in_batch_id = batch.id``. Транзакция: для каждой
    person'ы вызывается ``merge_engine.apply_merge`` (Phase 7+ — пока
    parser-service ``person_merger``).

    ``person_ids`` — JSONB-массив UUID'ов: какие именно persons
    участвовали в этом батче (читается без JOIN на decisions для
    UI-summary). Source of truth — всё-таки ``MergeDecision`` row'ы.

    ``apply_log_json`` — конкатенация ``dry_run_diff_json`` от ADR-0022
    (один на каждый per-person merge), лежит в JSONB как материал для
    возможного batched-undo. Phase 7 не реализует batched-undo — undo
    идёт через ``person_merge_logs`` (ADR-0022 §90-day undo).

    FK ``session_id → merge_sessions.id ON DELETE CASCADE``.
    FK ``applied_by_user_id → users.id ON DELETE SET NULL``.
    """

    __tablename__ = "merge_apply_batches"
    __table_args__ = (
        # «Все batch'и этой сессии в порядке применения» — UI history.
        Index(
            "ix_merge_apply_batches_session_id_applied_at",
            "session_id",
            "applied_at",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("merge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    person_ids: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    applied_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    applied_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    apply_log_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )


__all__ = [
    "ChosenSource",
    "DecisionMethod",
    "MergeApplyBatch",
    "MergeDecision",
    "MergeDecisionScope",
    "MergeRefKind",
    "MergeSession",
    "MergeSessionStatus",
]
