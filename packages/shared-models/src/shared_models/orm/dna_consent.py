"""DnaConsent — explicit consent record для DNA upload (ADR-0020).

Каждый kit имеет привязанный consent: snapshot terms-text, метка
времени согласия, опциональная метка revoke. **Не soft-deleted** —
ADR-0012 + ADR-0020 явно opt out из ADR-0003 для DNA-данных. Consent
row остаётся навсегда для GDPR audit-trail; revoked_at — только
indicator события.

Каскадный hard-delete `DnaTestRecord` рядом с `DnaConsent` происходит
**на сервисном уровне** (см. ADR-0020 §«Consent revocation flow»):
сервис удаляет blob через Storage, затем delete-row, затем set
`revoked_at`. FK с CASCADE здесь не используется намеренно — сервис
должен явно сделать каждый шаг с audit-логом.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class DnaConsent(IdMixin, Base):
    """Запись consent на загрузку и обработку DNA одного kit'а.

    Поля:
        tree_id: Дерево, к которому привязан kit (multi-tenant scoping).
        user_id: Пользователь, давший consent. Обычно владелец kit'а
            или родственник, который его передал в дерево.
        kit_owner_email: Email владельца DNA-данных (может отличаться
            от user_id, если родственник делегировал — например, дед
            дал свой kit внуку для работы в его дереве).
        consent_text: Snapshot текста consent terms на момент согласия.
            Юристы должны иметь возможность доказать, что пользователь
            видел именно эту версию (GDPR Art. 7).
        consented_at: Server-side timestamp согласия.
        revoked_at: Server-side timestamp revoke (None если активен).
            Hard-delete привязанных DnaTestRecord — отдельная операция
            сервиса (см. ADR-0020).
        consent_version: Идентификатор версии terms (semver или дата);
            помогает группировать consent по версии при изменении
            политики.
    """

    __tablename__ = "dna_consents"

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kit_owner_email: Mapped[str] = mapped_column(
        String(320),  # RFC 5321 max email length.
        nullable=False,
    )
    consent_text: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    consent_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="1.0",
        server_default="1.0",
    )
    consented_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    @property
    def is_active(self) -> bool:
        """True если consent активен (revoked_at не установлен)."""
        return self.revoked_at is None
