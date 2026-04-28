"""TreeMembership и TreeInvitation — sharing-модель Phase 11.0.

См. ADR-0036 «Sharing & permissions model».

Замечание про legacy ``tree_collaborators``: таблица создана в первом
schema-миграции, но никогда не использовалась в API. Phase 11.0 не
мигрирует данные оттуда (пустая в любом deployed-окружении), а строит
параллельный набор таблиц с расширенным контрактом — invited_by,
accepted_at, revoked_at + invitation tokens. Дроп legacy-таблицы —
отдельной миграцией после Phase 11.1, когда станет ясно, что новый
flow покрывает все use-case'ы.
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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared_models.base import Base
from shared_models.enums import TreeRole
from shared_models.mixins import IdMixin, TimestampMixin

if TYPE_CHECKING:
    from shared_models.orm.tree import Tree
    from shared_models.orm.user import User


class TreeMembership(IdMixin, TimestampMixin, Base):
    """Активное членство пользователя в дереве с конкретной ролью.

    Lifecycle:

    * Создаётся либо при ``POST /trees`` (неявно, role=OWNER) — этот PR
      делает только новые trees через явный flow; уже-существующие
      trees получают OWNER-membership backfill-миграцией.
    * Создаётся при accept'е ``TreeInvitation`` (role из invitation).
    * Удаляется (soft через ``revoked_at``) — owner revoke'нул access
      или пользователь сам ушёл.

    Constraints:

    * ``UNIQUE (tree_id, user_id)`` — один user может быть в дереве
      ровно одной ролью одновременно.
    * Partial unique index ``ON (tree_id) WHERE role='owner' AND
      revoked_at IS NULL`` — ровно один OWNER на дерево (DB-level гарантия,
      без application-side race condition'ов).
    """

    __tablename__ = "tree_memberships"
    __table_args__ = (
        UniqueConstraint(
            "tree_id",
            "user_id",
            name="uq_tree_memberships_tree_id_user_id",
        ),
        # Partial unique index — Postgres-специфика, выражение в миграции:
        # CREATE UNIQUE INDEX uq_tree_memberships_one_owner_per_tree
        # ON tree_memberships (tree_id) WHERE role='owner' AND revoked_at IS NULL.
        Index("ix_tree_memberships_tree", "tree_id"),
        Index("ix_tree_memberships_user", "user_id"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TreeRole.VIEWER.value,
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Кто пригласил. NULL для backfilled OWNER-записей и для accept-by-self.",
    )
    accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Когда membership стал активным. NULL = pending — например, "
            "пригласили существующего user'а по email, но он ещё не принял. "
            "В Phase 11.0 invitation-flow всегда создаёт membership с "
            "accepted_at=now() сразу при accept'е, поэтому NULL практически "
            "не встречается, но колонка зарезервирована для in-app-pending UI."
        ),
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Soft-revoke. Запись остаётся в audit-целях, но perm-check'и игнорируют.",
    )

    # ---- relationships -----------------------------------------------------
    tree: Mapped[Tree] = relationship(
        "Tree",
        foreign_keys=[tree_id],
        lazy="raise",
    )
    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
        lazy="raise",
    )
    inviter: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[invited_by],
        lazy="raise",
    )


class TreeInvitation(IdMixin, TimestampMixin, Base):
    """Email-приглашение в дерево.

    В отличие от ``TreeMembership``, инвайт может быть выписан на email
    адрес, под которым пока нет ``users``-записи: invitee получает
    accept-link на email; при логине/регистрации сервер связывает его
    user_id с invitation и создаёт TreeMembership.

    Lifecycle:

    * ``created`` — created_at, ``token`` сгенерирован (UUID v4).
    * ``revoked`` — owner revoke'нул через DELETE /invitations/{id};
      ``revoked_at`` выставлен. accept-flow возвращает 410 Gone.
    * ``accepted`` — invitee пришёл на /invitations/{token}/accept,
      залогинен как user, ``accepted_at`` выставлен, создан Membership.
      Дальнейший accept того же token идемпотентно отдаёт 200 OK
      (свой membership), но не создаёт второй.

    Token — секрет одной-цели; revealed только в URL'е приглашения,
    в DB хранится в чистом виде (Phase 11.1 — рассмотреть hash). Для
    MVP надёжность достаточная: 128 бит uuid + expires_at.
    """

    __tablename__ = "tree_invitations"
    __table_args__ = (
        Index("ix_tree_invitations_tree", "tree_id"),
        Index("ix_tree_invitations_token", "token", unique=True),
        Index("ix_tree_invitations_invitee_email", "invitee_email"),
    )

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="CASCADE"),
        nullable=False,
    )
    inviter_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Owner, выписавший приглашение. RESTRICT — нельзя удалить пригласившего без cleanup'а.",
    )
    invitee_email: Mapped[str] = mapped_column(
        String(254),
        nullable=False,
        comment=(
            "Email, на который послано приглашение. Lowercase + trimmed. "
            "RFC 5321: 254 символа максимум для email. Не уникален: один "
            "адрес может быть приглашён в разные деревья."
        ),
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=TreeRole.VIEWER.value,
    )
    token: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
        unique=True,
        comment=(
            "Случайный UUID v4. Кладётся в URL приглашения "
            "(``/invitations/{token}/accept``), вход в acceptance-flow."
        ),
    )
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "TTL — после этого момента accept-эндпоинт возвращает 410. "
            "По умолчанию 14 дней с момента создания (выставляется приложением)."
        ),
    )
    accepted_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Кто accept'нул (NULL до accept'а или если user удалён).",
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ---- relationships -----------------------------------------------------
    tree: Mapped[Tree] = relationship(
        "Tree",
        foreign_keys=[tree_id],
        lazy="raise",
    )
    inviter: Mapped[User] = relationship(
        "User",
        foreign_keys=[inviter_user_id],
        lazy="raise",
    )
    accepted_by: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[accepted_by_user_id],
        lazy="raise",
    )

    @property
    def is_active(self) -> bool:
        """Pending = не revoked, не accepted, не expired (на момент чтения)."""
        if self.revoked_at is not None:
            return False
        if self.accepted_at is not None:
            return False
        return self.expires_at > dt.datetime.now(dt.UTC)

    DEFAULT_TTL_DAYS: int = 14
    """TTL приглашения по умолчанию (дней) — для callsite'ов которые создают."""
