"""Consent CRUD endpoints (см. ADR-0020 §«Consent revocation flow»).

POST /consents          — создать consent.
GET  /consents/{id}     — прочитать consent metadata.
DELETE /consents/{id}   — revoke consent + cascade hard-delete blob'ов.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models.orm import DnaConsent, DnaTestRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.config import Settings, get_settings
from dna_service.database import get_session
from dna_service.schemas import ConsentCreate, ConsentResponse
from dna_service.services.storage import LocalFilesystemStorage

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


def _to_response(consent: DnaConsent) -> ConsentResponse:
    return ConsentResponse(
        id=consent.id,
        tree_id=consent.tree_id,
        user_id=consent.user_id,
        kit_owner_email=consent.kit_owner_email,
        consent_version=consent.consent_version,
        consented_at=consent.consented_at,
        revoked_at=consent.revoked_at,
        is_active=consent.is_active,
    )


@router.post(
    "/consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["consents"],
)
async def create_consent(
    payload: ConsentCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentResponse:
    """Создать consent record."""
    consent = DnaConsent(
        tree_id=payload.tree_id,
        user_id=payload.user_id,
        kit_owner_email=payload.kit_owner_email,
        consent_text=payload.consent_text,
        consent_version=payload.consent_version,
    )
    session.add(consent)
    await session.flush()
    await session.refresh(consent)
    _LOG.debug(
        "consent created: id=%s tree=%s user=%s version=%s",
        consent.id,
        consent.tree_id,
        consent.user_id,
        consent.consent_version,
    )
    return _to_response(consent)


@router.get(
    "/consents/{consent_id}",
    response_model=ConsentResponse,
    tags=["consents"],
)
async def get_consent(
    consent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConsentResponse:
    """Прочитать metadata consent."""
    consent = await session.get(DnaConsent, consent_id)
    if consent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consent not found")
    return _to_response(consent)


@router.delete(
    "/consents/{consent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["consents"],
)
async def revoke_consent(
    consent_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Revoke consent + cascade hard-delete всех привязанных blob'ов.

    Per ADR-0020 §«Consent revocation flow»:
        1. Найти все DnaTestRecord с этим consent_id.
        2. Удалить blob через Storage (idempotent).
        3. Hard-delete DnaTestRecord rows.
        4. Set revoked_at на consent record.
        5. (Audit-log factum-only — добавим в Phase 6.2.x).
    """
    consent = await session.get(DnaConsent, consent_id)
    if consent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consent not found")

    if consent.revoked_at is not None:
        # Идемпотентно: повторный revoke — no-op, 204.
        return

    storage = LocalFilesystemStorage(settings.storage_root)

    rows = (
        (await session.execute(select(DnaTestRecord).where(DnaTestRecord.consent_id == consent_id)))
        .scalars()
        .all()
    )
    deleted_blobs = 0
    for record in rows:
        try:
            await storage.delete(record.storage_path)
            deleted_blobs += 1
        except Exception:
            _LOG.warning("storage delete failed during revoke", exc_info=True)
        await session.delete(record)

    consent.revoked_at = dt.datetime.now(dt.UTC)
    await session.flush()

    _LOG.debug(
        "consent revoked: id=%s blobs_deleted=%d records_deleted=%d",
        consent_id,
        deleted_blobs,
        len(rows),
    )
