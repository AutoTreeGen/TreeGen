"""Consent CRUD endpoints (см. ADR-0020 §«Consent revocation flow»).

POST   /consents                  — создать consent.
GET    /consents/{id}             — прочитать consent metadata.
DELETE /consents/{id}             — revoke consent + cascade hard-delete blob'ов.
GET    /users/{user_id}/consents  — все consent'ы пользователя (active + revoked).

Audit-log записи пишутся **явно** на сервисном уровне, потому что
``DnaConsent`` и ``DnaTestRecord`` намеренно не подключены к
``register_audit_listeners`` (ADR-0012 — DNA opts out of ADR-0003
soft-delete + auto-audit). Каждый consent INSERT, REVOKE и каждое
cascade hard-delete ``DnaTestRecord`` записывается отдельной строкой
в audit_log с минимально достаточным diff'ом.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models.enums import ActorKind, AuditAction
from shared_models.orm import AuditLog, DnaConsent, DnaTestRecord
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


def _consent_audit_diff_insert(consent: DnaConsent) -> dict[str, Any]:
    """Diff для consent INSERT — без PII (kit_owner_email, consent_text)."""
    return {
        "before": None,
        "after": {
            "id": str(consent.id),
            "tree_id": str(consent.tree_id),
            "user_id": str(consent.user_id),
            "consent_version": consent.consent_version,
            "consented_at": consent.consented_at.isoformat() if consent.consented_at else None,
        },
        "fields": ["id", "tree_id", "user_id", "consent_version", "consented_at"],
    }


def _consent_audit_diff_revoke(revoked_at: dt.datetime) -> dict[str, Any]:
    """Diff для consent UPDATE при revoke — только revoked_at."""
    return {
        "fields": ["revoked_at"],
        "changes": {
            "revoked_at": {
                "before": None,
                "after": revoked_at.isoformat(),
            },
        },
    }


def _test_record_factum_diff() -> dict[str, Any]:
    """Factum-only diff для DnaTestRecord hard-delete.

    Per `DnaTestRecord` ORM docstring: «без kit_id / sha256 /
    storage_path в audit, чтобы deletion действительно стирала
    associativity» (ADR-0012). Ровно факт удаления, без атрибутов.
    """
    return {
        "fields": [],
        "factum": "hard_delete",
    }


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
    """Создать consent record + audit-log INSERT entry."""
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

    session.add(
        AuditLog(
            tree_id=consent.tree_id,
            entity_type=DnaConsent.__tablename__,
            entity_id=consent.id,
            action=AuditAction.INSERT.value,
            actor_user_id=consent.user_id,
            actor_kind=ActorKind.USER.value,
            reason="consent.create",
            diff=_consent_audit_diff_insert(consent),
        )
    )
    await session.flush()

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


@router.get(
    "/users/{user_id}/consents",
    response_model=list[ConsentResponse],
    tags=["consents"],
)
async def list_user_consents(
    user_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[ConsentResponse]:
    """Все consent-записи пользователя — active и revoked, новые сверху.

    В Phase 6.2 endpoint принимает user_id в path-параметре (auth
    middleware ещё не подключён). После Phase 6.x можно будет
    переименовать в ``/me/consents`` с auth-derived user_id.
    """
    rows = (
        (
            await session.execute(
                select(DnaConsent)
                .where(DnaConsent.user_id == user_id)
                .order_by(DnaConsent.consented_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(c) for c in rows]


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

    Per ADR-0020 §«Consent revocation flow» + ADR-0012 (hard-delete):
        1. Найти все DnaTestRecord с этим consent_id.
        2. Удалить blob через Storage (idempotent).
        3. Hard-delete DnaTestRecord rows + factum-only audit-log.
        4. Set revoked_at на consent record + audit-log UPDATE.
    """
    consent = await session.get(DnaConsent, consent_id)
    if consent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="consent not found")

    if consent.revoked_at is not None:
        # Идемпотентно: повторный revoke — no-op, 204. Audit-log не пишем —
        # реальной мутации не было.
        return

    storage = LocalFilesystemStorage(settings.storage_root)

    rows = (
        (await session.execute(select(DnaTestRecord).where(DnaTestRecord.consent_id == consent_id)))
        .scalars()
        .all()
    )
    deleted_blobs = 0
    for record in rows:
        record_id = record.id
        record_tree_id = record.tree_id
        try:
            await storage.delete(record.storage_path)
            deleted_blobs += 1
        except Exception:
            _LOG.warning("storage delete failed during revoke", exc_info=True)
        await session.delete(record)
        # Factum-only audit per DnaTestRecord ORM docstring (ADR-0012).
        session.add(
            AuditLog(
                tree_id=record_tree_id,
                entity_type=DnaTestRecord.__tablename__,
                entity_id=record_id,
                action=AuditAction.DELETE.value,
                actor_user_id=consent.user_id,
                actor_kind=ActorKind.USER.value,
                reason="consent.revoke.cascade_test_record",
                diff=_test_record_factum_diff(),
            )
        )

    revoked_at = dt.datetime.now(dt.UTC)
    consent.revoked_at = revoked_at
    session.add(
        AuditLog(
            tree_id=consent.tree_id,
            entity_type=DnaConsent.__tablename__,
            entity_id=consent.id,
            action=AuditAction.UPDATE.value,
            actor_user_id=consent.user_id,
            actor_kind=ActorKind.USER.value,
            reason="consent.revoke",
            diff=_consent_audit_diff_revoke(revoked_at),
        )
    )
    await session.flush()

    _LOG.debug(
        "consent revoked: id=%s blobs_deleted=%d records_deleted=%d",
        consent_id,
        deleted_blobs,
        len(rows),
    )
