"""ObjectStorage helpers для bundle blobs (Phase 24.4).

Layout:
    ``relationship-bundles/{tree_id}/{job_id}.{ext}`` — финальный bundle.

Tree-prefix → GDPR-erasure-by-prefix симметрично 24.3
``relationship-reports/{tree_id}/{report_id}.pdf`` и 15.6
``court-ready-reports/{person_id}/...``.
"""

from __future__ import annotations

import uuid

from shared_models.storage import ObjectStorage, build_storage_from_env

from report_service.bundles.data import BundleOutputFormat

_STORAGE_KEY_PREFIX: str = "relationship-bundles"
_CONTENT_TYPE_BY_FORMAT: dict[str, str] = {
    BundleOutputFormat.ZIP_OF_PDFS.value: "application/zip",
    BundleOutputFormat.CONSOLIDATED_PDF.value: "application/pdf",
}
_EXTENSION_BY_FORMAT: dict[str, str] = {
    BundleOutputFormat.ZIP_OF_PDFS.value: "zip",
    BundleOutputFormat.CONSOLIDATED_PDF.value: "pdf",
}


def get_bundle_storage() -> ObjectStorage:
    """Construct storage backend by env. Mirrors 24.3 ``get_report_storage``."""
    return build_storage_from_env()


def storage_key(*, tree_id: uuid.UUID, job_id: uuid.UUID, output_format: str) -> str:
    """``relationship-bundles/{tree_id}/{job_id}.{ext}``."""
    ext = _EXTENSION_BY_FORMAT.get(output_format, "bin")
    return f"{_STORAGE_KEY_PREFIX}/{tree_id}/{job_id}.{ext}"


def content_type_for(output_format: str) -> str:
    return _CONTENT_TYPE_BY_FORMAT.get(output_format, "application/octet-stream")


__all__ = [
    "content_type_for",
    "get_bundle_storage",
    "storage_key",
]
