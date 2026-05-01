"""Chat sessions + messages — AI tree-chat persistence (Phase 10.7c).

Chat — это разговор пользователя с AI-ассистентом в контексте конкретного
дерева. Сессия — длинноживущий thread; сообщения — упорядоченный список
поворотов внутри. AI имеет доступ к 10.7a self-anchor + 10.7b ego-resolver
для разрешения «кто такая Двора» / «брат жены».

Service-table pattern (как audio_sessions / source_extractions): не наследует
``TreeEntityMixins``, не имеет provenance/version_id/confidence_score —
артефакт AI-вызова, не genealogy-факт. Удаление сессии — hard delete (CASCADE
по дереву + FK CASCADE на messages).

``anchor_person_id`` сохраняется как snapshot на момент создания сессии,
а не лук'апается из ``trees.owner_person_id`` каждый turn: владелец может
переустановить self-anchor посреди разговора, и references в старых
сообщениях должны остаться семантически валидными относительно того
anchor'а, для которого они были резолвлены.
"""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin


class ChatMessageRole(enum.StrEnum):
    """Роль автора одного сообщения в чат-сессии.

    - ``USER``: ввод пользователя.
    - ``ASSISTANT``: ответ Claude.
    - ``SYSTEM``: системные сообщения (приветствие, error notice). Не
      отправляются в Anthropic — UI-only маркер.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatSession(IdMixin, TimestampMixin, Base):
    """Один чат-thread пользователя в контексте дерева.

    FK ``tree_id → trees.id ON DELETE CASCADE``: при удалении дерева сессии
    чистятся вместе с messages (ON DELETE CASCADE на самом FK + сами
    messages CASCADE'ятся по session_id).

    FK ``user_id → users.id ON DELETE RESTRICT``: пользователь не может
    быть удалён, пока у него остаются чат-сессии — иначе теряем audit-trail.
    GDPR-erasure (ADR-0049) удаляет чаты до user'а.

    FK ``anchor_person_id → persons.id ON DELETE SET NULL``: если anchor-
    person удалён из дерева, сессия сохраняется без anchor'а (старые
    references остаются в БД как есть; новый turn не сможет резолвить
    relative refs, но видеть и читать историю можно).
    """

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_tree_id", "tree_id"),
        Index("ix_chat_sessions_user_id", "user_id"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    anchor_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("persons.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Заголовок — auto-generated из первого user-сообщения после первого
    # turn; до тех пор NULL. UI показывает "Untitled chat" / fallback.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ChatMessage(IdMixin, TimestampMixin, Base):
    """Одно сообщение внутри чат-сессии.

    Сообщения immutable — после insert не редактируются; ``updated_at``
    заполняется server-side, но фактически совпадает с ``created_at``
    (TimestampMixin наследуется ради unification, не из-за реальной
    мутабельности).

    FK ``session_id → chat_sessions.id ON DELETE CASCADE``.

    ``references`` — JSONB-массив объектов
    ``{"person_id": uuid, "mention_text": str, "confidence": float}``
    (сериализуется напрямую из ResolvedPerson из 10.7b ego_resolver'а).
    Default — пустой массив; ассистент-сообщение без references — обычное
    дело (small-talk, отказ).
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        # Composite: load session messages in chronological order — самый
        # частый и узкий запрос (один SELECT per page-load).
        Index("ix_chat_messages_session_id_created_at", "session_id", "created_at"),
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')",
            name="ck_chat_messages_role",
        ),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    references: Mapped[list[dict[str, Any]]] = mapped_column(
        "references_jsonb",
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )


__all__ = ["ChatMessage", "ChatMessageRole", "ChatSession"]
