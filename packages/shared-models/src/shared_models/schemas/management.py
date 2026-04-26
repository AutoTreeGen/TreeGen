"""DTO для управления: User, Tree, ImportJob."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import EmailStr, Field

from shared_models.enums import (
    ImportJobStatus,
    ImportSourceKind,
    TreeVisibility,
)
from shared_models.schemas.common import SchemaBase, SoftTimestamps

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
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    created_at: dt.datetime
