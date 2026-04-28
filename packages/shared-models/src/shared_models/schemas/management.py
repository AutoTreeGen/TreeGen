"""DTO для управления: User, Tree, ImportJob."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import EmailStr, Field

from shared_models.enums import (
    ImportJobStatus,
    ImportSourceKind,
    TreeVisibility,
)
from shared_models.schemas.common import SchemaBase, SoftTimestamps

# Канонические стадии async-импорта (Phase 3.5).
# Worker публикует ProgressEvent на каждом переходе между ними.
# UI (apps/web) рисует step-list и финализирует прогресс на терминальной
# стадии. Список фиксирован здесь, чтобы UI и backend не разъезжались по
# свободным строкам.
ImportStage = Literal[
    "queued",
    "uploading",
    "parsing",
    "persons",
    "families",
    "events",
    "places",
    "sources",
    "citations",
    "multimedia",
    "audit",
    "succeeded",
    "failed",
    "cancelled",
]

# ---- User ----------------------------------------------------------------


class UserBase(SchemaBase):
    """Общие поля пользователя."""

    email: EmailStr
    external_auth_id: str
    display_name: str | None = None
    locale: str = "en"


class UserCreate(UserBase):
    """Создание пользователя (обычно из webhook auth-провайдера)."""


class UserRead(UserBase, SoftTimestamps):
    """Ответ API: пользователь с системными полями."""

    id: uuid.UUID


# ---- Tree ----------------------------------------------------------------


class TreeBase(SchemaBase):
    """Общие поля дерева."""

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    visibility: TreeVisibility = TreeVisibility.PRIVATE
    default_locale: str = "en"
    settings: dict[str, Any] = Field(default_factory=dict)


class TreeCreate(TreeBase):
    """Создание дерева. owner_user_id берётся из auth-контекста."""


class TreeUpdate(SchemaBase):
    """Обновление дерева (PATCH-семантика)."""

    name: str | None = None
    description: str | None = None
    visibility: TreeVisibility | None = None
    default_locale: str | None = None
    settings: dict[str, Any] | None = None


class TreeRead(TreeBase, SoftTimestamps):
    """Ответ API: дерево с владельцем и системными полями."""

    id: uuid.UUID
    owner_user_id: uuid.UUID
    version_id: int


# ---- ImportJob -----------------------------------------------------------


class ImportJobProgress(SchemaBase):
    """Снапшот прогресса async-импорта (Phase 3.5).

    Хранится в ``ImportJob.progress`` (jsonb) и публикуется в Redis
    pubsub-канал ``job-events:{job_id}`` для live-стрима через SSE.
    Последний published снапшот = последнее, что увидит UI на pull
    через ``GET /imports/{id}``.

    ``current`` / ``total`` — численный прогресс внутри стадии (например,
    кол-во обработанных PERS / общее в файле). На стадии ``parsing`` они
    могут быть None (парсер не отдаёт промежуточные счётчики).

    ``ts`` — серверное UTC-время публикации события. Помогает фронту
    отбросить out-of-order события из pubsub'а (они приходят упорядоченно
    в пределах канала, но переподключение SSE может смешать снапшоты с
    DB-snapshot'ом из ``GET /imports/{id}.progress``).
    """

    stage: ImportStage = Field(description="Текущая стадия импорта.")
    current: int | None = Field(
        default=None,
        ge=0,
        description="Кол-во обработанных элементов внутри стадии (если известно).",
    )
    total: int | None = Field(
        default=None,
        ge=0,
        description="Общее кол-во элементов на стадии (если известно).",
    )
    message: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable сообщение для UI (например, 'Loading 1234 persons').",
    )
    ts: dt.datetime = Field(description="UTC-время публикации события (сервер).")


class ImportJobRead(SchemaBase):
    """Ответ API: статус импорт-джоба."""

    id: uuid.UUID
    tree_id: uuid.UUID
    created_by_user_id: uuid.UUID | None
    source_kind: ImportSourceKind
    source_filename: str | None
    source_size_bytes: int | None
    source_sha256: str | None
    status: ImportJobStatus
    stats: dict[str, Any]
    errors: list[dict[str, Any]]
    progress: ImportJobProgress | None = None
    cancel_requested: bool = False
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime
