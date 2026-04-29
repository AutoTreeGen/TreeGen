"""PublicTreeShare — публичная read-only ссылка на дерево (Phase 11.2).

Owner создаёт публичный share-link через ``POST /trees/{id}/public-share``;
endpoint возвращает ``share_token`` (URL-safe ~20 chars). Любой неаутентифи-
цированный пользователь, имея этот URL, видит read-only представление
дерева через ``GET /public/trees/{token}``. Privacy-фильтры применяются
на server-side:

* DNA-данные ВЫРЕЗАНЫ полностью — ни matches, ни kits, ни consent rows.
* Persons с ``is_alive=True`` анонимизированы — first_name/last_name
  заменяется на «Living relative», даты-факты обрезаны до года.
* Provenance, sources, citations доступны (это уже не PII).

См. ADR-0047 «Public tree share — privacy model».

Lifecycle:

* ``created`` — owner POST'ит, row создаётся с ``share_token`` (random
  base64url) и опциональным ``expires_at``. Только owner может создавать.
* ``revoked`` — owner DELETE'ит → ``revoked_at`` выставляется. Дальше
  ``GET /public/trees/{token}`` отдаёт 404 (не 410, чтобы не палить
  факт ранее существовавшего share).
* ``expired`` — если ``expires_at`` прошёл, тот же 404.

Token хранится в plaintext: 120 бит энтропии (token_urlsafe(15)), brute-
force невозможен; в логах токены MASKED. Hash-storage — Phase 11.3 если
threat-модель потребует.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from shared_models.orm.tree import Tree
    from shared_models.orm.user import User


class PublicTreeShare(IdMixin, TimestampMixin, Base):
    """Public read-only share-link для дерева.

    Constraints:

    * ``share_token`` UNIQUE — приватность зависит от unguessable token'а;
      коллизия (хоть и астрономически маловероятная) ломает контракт.
    * Один tree может иметь много public shares (например, owner ротирует
      токен — старый ``revoked_at``, новый создан). Партиал-unique
      «один активный share на tree» НЕ enforce'ится — UI это позволяет
      сознательно (smooth rotation без race condition).

    Token-формат: ``secrets.token_urlsafe(15)`` → 20 chars base64url
    (~120 bits энтропии). Хранится в plaintext в DB; в логах MASKED
    (первые 6 chars + ``...``).
    """

    __tablename__ = "public_tree_shares"
    __table_args__ = (
        # UNIQUE индекс на токене — primary lookup path для public-эндпоинта.
        Index("ix_public_tree_shares_token", "share_token", unique=True),
        Index("ix_public_tree_shares_tree", "tree_id"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    share_token: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment=(
            "URL-safe random token (~20 chars, ~120 bits entropy). Uniqueness "
            "enforced by ix_public_tree_shares_token. App-side: secrets.token_urlsafe(15)."
        ),
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        comment="Owner, выписавший share. CASCADE — при hard-delete user'а share не должен переживать.",
    )
    expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Опциональный TTL. NULL = never expires (owner ревокейт явно). "
            "После expires_at GET /public/trees/{token} возвращает 404."
        ),
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft-revoke. Запись остаётся в audit-целях; GET даёт 404.",
    )

    # ---- relationships -----------------------------------------------------
    tree: Mapped[Tree] = relationship(
        "Tree",
        foreign_keys=[tree_id],
        lazy="raise",
    )
    creator: Mapped[User] = relationship(
        "User",
        foreign_keys=[created_by_user_id],
        lazy="raise",
    )

    @property
    def is_active(self) -> bool:
        """Активный share = не revoked И не expired (на момент чтения)."""
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at <= dt.datetime.now(dt.UTC))
