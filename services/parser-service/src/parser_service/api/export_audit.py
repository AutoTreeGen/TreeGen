"""Export Audit endpoint (Phase 5.9).

``POST /api/v1/gedcom/audit-export`` — pre-export loss preview. Принимает
multipart upload (.ged) + список target_platforms; возвращает JSON со
списком findings per-platform. Stateless: ничего не пишется ни в БД, ни
на диск (кроме UploadFile-spool, который FastAPI чистит сам).

Почему multipart, а не ``import_job_id``: брифовый ``import_jobs.parsed_data``
column в текущей main отсутствует — мы не персистим распарсенный документ
после импорта (только raw blob через worker'а с уже истёкшим tempfile'ом).
Stateless multipart-upload — наиболее верное к спирту анти-дрифта
("DO NOT add a database table for audits. Stateless on-demand").

Auth required (через ``_AUTH_DEPS`` в main.py). Никакого permission-gate
по tree_id здесь нет — пользователь скармливает свой собственный файл,
проверять ему права на чужие деревья не нужно.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from gedcom_parser import GedcomDocument, GedcomError, parse_bytes
from gedcom_parser.audit import ExportAudit, TargetPlatform, audit_export
from pydantic import BaseModel, ConfigDict

from parser_service.config import Settings, get_settings

router = APIRouter()


class AuditExportResponse(BaseModel):
    """Ответ ``POST /api/v1/gedcom/audit-export``: per-platform audits."""

    audits: dict[TargetPlatform, ExportAudit]

    model_config = ConfigDict(extra="forbid")


@router.post(
    "/api/v1/gedcom/audit-export",
    response_model=AuditExportResponse,
    summary="Pre-export audit: predict losses when exporting GEDCOM to target platforms.",
    description=(
        "Phase 5.9 — stateless audit. Принимает GEDCOM-файл (multipart) и "
        "список target_platforms, возвращает per-platform findings (severity "
        "lost / transformed / warning) поверх правил Phase 5.6. Read-only: "
        "никогда не мутирует входной файл, никогда не пишет в БД."
    ),
)
async def audit_export_endpoint(
    file: UploadFile,
    target_platforms: Annotated[list[TargetPlatform], Form()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuditExportResponse:
    """Распарсить upload, прогнать audit_export() per target, вернуть JSON.

    Валидации:
    * расширение ``.ged`` / ``.gedcom`` — иначе 400;
    * размер ≤ ``settings.max_upload_mb`` — иначе 413;
    * непустой ``target_platforms`` — иначе 400;
    * парсинг fail → 422 с сообщением парсера.
    """
    if not target_platforms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one target_platform is required.",
        )

    if not file.filename or not file.filename.lower().endswith((".ged", ".gedcom")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected .ged or .gedcom file",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    contents = await file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large; max {settings.max_upload_mb} MB",
        )

    try:
        records, _encoding = parse_bytes(contents, lenient=True)
        document = GedcomDocument.from_records(records)
    except (GedcomError, UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"GEDCOM parse failed: {exc}",
        ) from exc

    # Дедуплицируем платформы — клиент мог прислать одну дважды; результат
    # детерминирован: один audit per-target (последний выигрывает).
    audits: dict[TargetPlatform, ExportAudit] = {}
    for target in target_platforms:
        audits[target] = audit_export(document, target)
    return AuditExportResponse(audits=audits)
