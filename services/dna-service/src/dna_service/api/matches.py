"""Match endpoint: POST /matches — compute pairwise match для двух
загруженных test_records (per ADR-0020 §«Phase 6.2 scope»).

Phase 6.2 ограничения:
    - Оба test_record должны принадлежать одному `user_id`. Cross-user —
      Phase 6.3 с future ADR-0021.
    - Оба consent должны быть активны (revoked_at IS NULL).
    - Encryption_scheme="none" — service ожидает plaintext blob и парсит
      его как 23andMe/Ancestry. encrypted scheme → Phase 6.2.x.
    - Genetic map путь берётся из ENV `DNA_SERVICE_GENETIC_MAP_DIR`
      (если не задан — 400 с понятным сообщением).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status
from shared_models.orm import DnaConsent, DnaTestRecord
from sqlalchemy.ext.asyncio import AsyncSession

from dna_service.config import Settings, get_settings
from dna_service.database import get_session
from dna_service.schemas import MatchRequest, MatchResponse
from dna_service.services.matcher import run_match
from dna_service.services.storage import LocalFilesystemStorage

router = APIRouter()

_LOG: Final = logging.getLogger(__name__)


def _genetic_map_dir() -> Path:
    """Каталог genetic map берётся из ENV; ошибка если не задан."""
    raw = os.environ.get("DNA_SERVICE_GENETIC_MAP_DIR")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "DNA_SERVICE_GENETIC_MAP_DIR is not set — matching is unavailable. "
                "See packages/dna-analysis/scripts/download_genetic_map.py."
            ),
        )
    path = Path(raw)
    if not path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"DNA_SERVICE_GENETIC_MAP_DIR={raw} is not a directory",
        )
    return path


@router.post(
    "/matches",
    response_model=MatchResponse,
    tags=["matches"],
)
async def compute_match(
    payload: MatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MatchResponse:
    """Compute pairwise DNA match для двух test_records одного пользователя."""
    test_a = await session.get(DnaTestRecord, payload.test_a_id)
    test_b = await session.get(DnaTestRecord, payload.test_b_id)
    if test_a is None or test_b is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="one or both test_records not found"
        )

    # Phase 6.2: same-user only. Cross-user — Phase 6.3.
    if test_a.user_id != test_b.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cross-user matching is not enabled in Phase 6.2 (see ADR-0020)",
        )

    # Оба consent должны быть активны.
    consent_a = await session.get(DnaConsent, test_a.consent_id)
    consent_b = await session.get(DnaConsent, test_b.consent_id)
    if (
        consent_a is None
        or consent_b is None
        or consent_a.revoked_at is not None
        or consent_b.revoked_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="match requires both consents to be active",
        )

    # Phase 6.2 поддерживает только plaintext-stored blobs.
    if test_a.encryption_scheme != "none" or test_b.encryption_scheme != "none":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=("encrypted-blob matching requires Phase 6.2.x (browser-side decrypt)"),
        )

    storage = LocalFilesystemStorage(settings.storage_root)
    blob_a = await storage.read(test_a.storage_path)
    blob_b = await storage.read(test_b.storage_path)

    derived = run_match(
        blob_a=blob_a,
        blob_b=blob_b,
        genetic_map_dir=_genetic_map_dir(),
    )

    segments_list = derived["shared_segments"]
    assert isinstance(segments_list, list)
    _LOG.debug(
        "match computed: a=%s b=%s segments=%d total_cm=%.2f",
        payload.test_a_id,
        payload.test_b_id,
        len(segments_list),
        derived["total_shared_cm"],
    )

    return MatchResponse(
        test_a_id=payload.test_a_id,
        test_b_id=payload.test_b_id,
        **derived,
    )
