"""DnaTestRecord — metadata одного загруженного DNA-блоба (ADR-0020).

Сами raw данные хранятся через `Storage` интерфейс
(`services/dna-service/services/storage.py`); БД держит только
метадату для discovery + integrity-check.

**Не soft-deleted** — ADR-0012 + ADR-0020 opt out из ADR-0003 для
DNA. Удаление row = hard delete + удаление файла на диске + audit-log
factum-only (без kit_id / sha256 / storage_path в audit, чтобы deletion
действительно стирала associativity).

`encryption_scheme` явно фиксирует, как именно зашифрован blob:
    - "none" — Phase 6.2 без encryption (только при
      `DNA_REQUIRE_ENCRYPTION=false`, сервис логирует warning).
    - "argon2id+aes256gcm" — Phase 6.2.x, browser-side encryption
      (ADR-0020 §«Phase 6.2.x»).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from shared_models.base import Base
from shared_models.mixins import IdMixin


class DnaTestRecord(IdMixin, Base):
    """Metadata одного загруженного encrypted DNA-блоба.

    Поля:
        tree_id: Дерево, к которому привязан blob.
        consent_id: FK на DnaConsent. RESTRICT — консент должен быть
            явно отозван через сервисный flow (с удалением blob),
            а не через прямой SQL DELETE.
        user_id: Пользователь, загрузивший blob (для quota и audit).
        storage_path: Путь к encrypted blob через `Storage` интерфейс.
            Для LocalFilesystemStorage — относительный путь от
            `DNA_STORAGE_ROOT`.
        size_bytes: Размер blob'а в байтах. Для quota и monitoring.
        sha256: SHA-256 от encrypted content. Tamper-detect + dedup.
        snp_count: Количество SNP в plaintext (известно после успешного
            парсинга на момент upload). Hint для UI и matching budget.
        provider: "23andme", "ancestry", etc. — see DnaPlatform enum.
        encryption_scheme: "none" | "argon2id+aes256gcm". См. ADR-0020.
        uploaded_at: Server-side timestamp upload.
    """

    __tablename__ = "dna_test_records"

    tree_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trees.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    consent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("dna_consents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    storage_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    snp_count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    encryption_scheme: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="none",
        server_default="none",
    )
    uploaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
