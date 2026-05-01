"""DNA upload endpoint (per ADR-0020 §«Phase 6.2 scope»).

POST /dna-uploads — multipart upload encrypted blob, требует активный
consent_id. При DNA_REQUIRE_ENCRYPTION=true (prod default) отвергает
любой blob без encryption-magic-header (формат — Phase 6.2.x).

Phase 6.2 без encryption (`require_encryption=false` для dev/CI):
сервис принимает raw 23andMe / Ancestry .txt контент, парсит его для
получения snp_count, пишет на диск как-есть и помечает
`encryption_scheme="none"`.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Annotated, Final

from dna_analysis.errors import UnsupportedFormatError
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from shared_models.orm import DnaConsent, DnaTestRecord
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.billing import require_feature
from dna_service.config import Settings, get_settings
from dna_service.database import get_session
from dna_service.schemas import TestRecordResponse
from dna_service.services.matcher import parse_blob
from dna_service.services.storage import LocalFilesystemStorage

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)

# Magic-header для encrypted blobs (Phase 6.2.x формат). До тех пор
# пока encryption не реализован, проверяем только presence на префиксе.
_ENCRYPTION_MAGIC: Final = b"ATGN-DNA-ENC1"


@router.post(
    "/dna-uploads",
    response_model=TestRecordResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["uploads"],
)
async def upload_dna(
    consent_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
    # Phase 12.0: DNA-фичи доступны только на Pro/Premium. При
    # billing_enabled=false dependency пропускает (no-op).
    _entitlement: Annotated[None, require_feature("dna_enabled")] = None,
) -> TestRecordResponse:
    """Принять загрузку DNA blob и привязать к активному consent."""
    # 1. Consent должен существовать и быть активным.
    consent = await session.get(DnaConsent, consent_id)
    if consent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consent not found")
    if consent.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="consent has been revoked")

    # 2. Прочитать blob (с size-лимитом).
    max_bytes = settings.max_upload_mb * 1024 * 1024
    blob = await file.read(max_bytes + 1)
    if len(blob) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload exceeds {settings.max_upload_mb} MB limit",
        )
    if not blob:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")

    # 3. Encryption gate.
    is_encrypted = blob.startswith(_ENCRYPTION_MAGIC)
    if settings.require_encryption and not is_encrypted:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "DNA_SERVICE_REQUIRE_ENCRYPTION is enabled — uploads must include "
                "the encryption magic header"
            ),
        )
    encryption_scheme = "argon2id+aes256gcm" if is_encrypted else "none"

    # 4. Parse для получения snp_count + provider (только при plaintext;
    #    encrypted blobs парсятся клиентом до encryption и метаdata
    #    приходит в multipart form в Phase 6.2.x).
    if encryption_scheme == "none":
        try:
            test = parse_blob(blob)
        except UnsupportedFormatError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        snp_count = len(test.snps)
        provider = test.provider.value
        # Warning header для prod-alerting если случайно работает в plaintext-mode.
        response.headers["X-Warning"] = "dna-encryption-disabled"
        _LOG.warning(
            "DNA upload accepted in plaintext mode (require_encryption=false); "
            "do NOT use this in production"
        )
    else:
        # Phase 6.2.x: snp_count + provider должны передаваться отдельным
        # form field (клиент знает их до encryption). Пока заглушка.
        snp_count = 0
        provider = "encrypted"

    # 5. Записать blob в storage.
    storage = LocalFilesystemStorage(settings.storage_root)
    storage_path = storage.generate_path()
    await storage.write(storage_path, blob)

    sha256 = hashlib.sha256(blob).hexdigest()

    # 6. Создать DnaTestRecord.
    record = DnaTestRecord(
        tree_id=consent.tree_id,
        consent_id=consent.id,
        user_id=consent.user_id,
        storage_path=storage_path,
        size_bytes=len(blob),
        sha256=sha256,
        snp_count=snp_count,
        provider=provider,
        encryption_scheme=encryption_scheme,
    )
    session.add(record)
    await session.flush()
    await session.refresh(record)

    _LOG.debug(
        "DNA blob uploaded: record_id=%s size=%d snp_count=%d scheme=%s sha256=%s",
        record.id,
        record.size_bytes,
        record.snp_count,
        record.encryption_scheme,
        record.sha256[:8],
    )

    return TestRecordResponse(
        id=record.id,
        tree_id=record.tree_id,
        consent_id=record.consent_id,
        user_id=record.user_id,
        size_bytes=record.size_bytes,
        sha256=record.sha256,
        snp_count=record.snp_count,
        provider=record.provider,
        encryption_scheme=record.encryption_scheme,
        uploaded_at=record.uploaded_at,
    )
