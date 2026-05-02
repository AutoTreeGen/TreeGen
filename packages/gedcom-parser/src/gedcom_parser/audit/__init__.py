"""GEDCOM Export Audit (Phase 5.9).

Pre-export simulation: «что вы потеряете при экспорте на платформу X?».
Тонкий слой поверх Phase 5.6 Compatibility Simulator: те же правила,
но output reshape'нут под evidence-первый UI (severity ``lost`` /
``transformed`` / ``warning``, привязки ``person_id``/``family_id``/
``source_id``, suggested_action для пользователя).

Высокоуровневое API:

    >>> from gedcom_parser import parse_document_file
    >>> from gedcom_parser.audit import audit_export, TargetPlatform
    >>> doc = parse_document_file("tree.ged")
    >>> result = audit_export(doc, TargetPlatform.ancestry)
    >>> result.summary
    {'lost': 12, 'transformed': 4, 'warning': 3}

Audit стейтлесс и read-only: никогда не мутирует ``doc``, никогда не
пишет в БД. Caller персистит результат сам, если нужно.
"""

from __future__ import annotations

from gedcom_parser.audit.export_audit import (
    AuditFinding,
    AuditSeverity,
    ExportAudit,
    TargetPlatform,
    audit_export,
)

__all__ = [
    "AuditFinding",
    "AuditSeverity",
    "ExportAudit",
    "TargetPlatform",
    "audit_export",
]
