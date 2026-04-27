"""PersonMergeLog — audit-trail для manual person merges (Phase 4.6, ADR-0022).

Каждый merge — отдельная row с полным `dry_run_diff_json` snapshot'ом.
Используется для:

* «View merge history» в UI карточки персоны;
* `POST /persons/merge/{merge_id}/undo` восстанавливает состояние,
  откатывая diff (90-дневное окно);
* GDPR / forensic audit при споре «как эти двое стали одной записью».

Не наследуется от ``SoftDeleteMixin``: лог-запись остаётся навсегда
(см. ADR-0022 §Retention). Поле ``undone_at`` — **отдельный indicator**
события «merge был откатан», не soft-delete этой строки. ``purged_at``
ставится фоновой job'ой, когда merged person действительно hard-
delete'ится после 90 дней — после этого undo невозможен.

Идемпотентность: уникальный partial-индекс по
``(tree_id, survivor_id, merged_id, confirm_token) WHERE undone_at IS NULL``
не даёт сделать два **активных** merge'а одной пары с одним токеном.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class PersonMergeLog(IdMixin, TimestampMixin, Base):
    """Запись об одном совершённом merge'е двух персон.

    Поля:
        tree_id: Дерево, в котором произошёл merge (multi-tenant scope).
        survivor_id: Persons.id выжившей стороны.
        merged_id: Persons.id поглощённой (soft-delete'нутой) стороны.
        merged_at: Когда merge выполнен (= ``created_at``, дублируется
            для индекса retention).
        merged_by_user_id: Пользователь-инициатор. NULL допускается до
            появления auth (Phase 4.2/4.x), потом станет обязательным.
        confirm_token: UUID, который клиент шлёт в payload commit'а.
            Идемпотентность: повторный POST с тем же токеном возвращает
            существующий лог row, не создавая нового.
        dry_run_diff_json: Полный snapshot изменений ровно в той форме,
            которую отдал ``preview``-endpoint (поле, события,
            переподключения family). Используется и для UI history view,
            и для undo.
        undone_at: Если merge был откатан — timestamp отката. NULL если
            ещё активен.
        undone_by_user_id: Пользователь-инициатор undo. Опционально
            (как и ``merged_by_user_id``).
        purged_at: Когда merged person hard-delete'нут фон-job'ой
            (через ≥90 дней). После этого undo возвращает 410 Gone.
    """

    __tablename__ = "person_merge_logs"
    __table_args__ = (
        # Уникальный partial-индекс: не даём два активных merge'а одной
        # пары с тем же token'ом. Откатанные / purged строки не блокируют.
        Index(
            "uq_person_merge_logs_active",
            "tree_id",
            "survivor_id",
            "merged_id",
            "confirm_token",
            unique=True,
            postgresql_where=text("undone_at IS NULL AND purged_at IS NULL"),
        ),
        # Поиск истории merge'ей конкретной персоны (как survivor или
        # как merged) для UI «View merge history».
        Index("ix_person_merge_logs_survivor", "tree_id", "survivor_id"),
        Index("ix_person_merge_logs_merged", "tree_id", "merged_id"),
        # Retention sweep: «найти все merge'и старше 90 дней без purged_at».
        Index("ix_person_merge_logs_merged_at", "merged_at"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    survivor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    merged_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="CASCADE"),
        nullable=False,
    )
    merged_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    merged_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirm_token: Mapped[str] = mapped_column(String(64), nullable=False)
    dry_run_diff_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    undone_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    undone_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    purged_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
