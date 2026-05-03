"""Completeness assertions validation layer (Phase 15.11b, ADR-0077)."""

from __future__ import annotations

from parser_service.completeness.validation import (
    AssertionRevokeContext,
    AssertionUpsertContext,
    OverrideRequiredError,
    SourceCrossTreeError,
    SourceDeletedError,
    SourceNotFoundError,
    SourceRequiredError,
    ValidationError,
    emit_completeness_audit,
    validate_assertion_create,
    validate_assertion_revoke,
)

__all__ = [
    "AssertionRevokeContext",
    "AssertionUpsertContext",
    "OverrideRequiredError",
    "SourceCrossTreeError",
    "SourceDeletedError",
    "SourceNotFoundError",
    "SourceRequiredError",
    "ValidationError",
    "emit_completeness_audit",
    "validate_assertion_create",
    "validate_assertion_revoke",
]
