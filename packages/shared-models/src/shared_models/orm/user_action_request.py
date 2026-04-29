"""UserActionRequest — request-row для пользовательских action'ов с side-effect (Phase 4.10b).

Phase 4.10b создаёт row'ы как stub (status=``pending``, без processing).
Phase 4.11 (Agent 5) добавит worker-handler, который:

* для ``kind='export'`` сгенерирует tar.gz с GEDCOM + DNA + provenance,
  закроет signed-URL'ом в storage, переведёт ``status='done'`` и пнёт
  user'а нотификацией;
* для ``kind='erasure'`` сделает hard-delete cascade всех данных user'а
  (см. ADR-0012 §«Right to be forgotten»), переведёт ``status='done'``.

UI Phase 4.10b использует только pending-row + ``GET /users/me/requests``
для списка (показать «у вас 1 export request на review»). Никакой
zip-генерации / cascade-delete'а здесь нет.

Schema-инвариант: один user может иметь сколько угодно завершённых
request'ов (history), но не более одного active (pending+processing) на
тот же ``kind``. Уникальный partial-index в alembic 0015 это enforce'ит
... через Python-level guard в endpoint'е. (Партиал unique даёт более
строгий contract; Phase 4.11 добавит, если потребуется.)

См. ADR-0038 §«Schema» для полной таблицы lifecycle и почему общая
таблица вместо отдельных ``export_requests`` / ``erasure_requests``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class UserActionRequest(IdMixin, TimestampMixin, Base):
    """Запрос на user-инициированное действие (export / erasure / ...).

    Атрибуты:
        user_id: Владелец request'а; FK ``users.id`` с ``ondelete='CASCADE'``.
            Если user удалён hard-delete'ом — request'ы тоже удаляются
            (для erasure это очевидно — request больше некому показывать).
        kind: ``"export"`` | ``"erasure"``. Расширяется через alembic check-
            constraint, не через postgres ENUM (см. ``shared_models.enums``
            §«как text»).
        status: lifecycle ``pending`` → ``processing`` → ``done`` / ``failed`` /
            ``cancelled``. Phase 4.10b создаёт только ``pending``; rest —
            Phase 4.11.
        request_metadata: jsonb для kind-specific параметров. Для
            ``export`` — формат (``"gedcom_tar_gz"``, default), filter'ы
            (later: tree_ids subset). Для ``erasure`` — confirmation token,
            email-confirm timestamp.
        processed_at: timestamp перевода в терминальный status. NULL пока
            request не обработан.
        error: текст ошибки (для ``status='failed'``). NULL иначе.
    """

    __tablename__ = "user_action_requests"
    __table_args__ = (
        # Phase 4.11c (миграция 0022) добавила ``ownership_transfer`` —
        # один request на одно дерево, которое нужно передать другому
        # active editor'у (см. ADR-0050).
        CheckConstraint(
            "kind IN ('export', 'erasure', 'ownership_transfer')",
            name="ck_user_action_requests_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'done', 'failed', 'cancelled')",
            name="ck_user_action_requests_status",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
