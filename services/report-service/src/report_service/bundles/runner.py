"""arq-worker entry: один bundle job → bundled blob в storage (Phase 24.4).

Alg (per ADR-0078 §"Worker logic"):

1. Load :class:`ReportBundleJob` row + permission re-check (defence-in-depth
   in case row was created by older request and permissions revoked since).
2. ``mark_running`` — status: queued → running, started_at = now.
3. For each pair:
   a. If ``claimed_relationship`` is NULL → ``auto_derive_claim`` (422-style
      LookupError → log to error_summary, increment failed_count, continue).
   b. Call :func:`generate_pdf_bytes_for_pair` (24.3 single source of truth).
   c. On success: ``increment_completed`` + capture
      :class:`PairResult` for bundle assembly.
   d. On failure: ``increment_failed`` + log entry to ``error_summary``,
      continue (don't poison entire job).
4. After all pairs:
   - If ALL failed → ``mark_failed``.
   - Else: assemble bundle (ZIP or consolidated PDF) → upload to ObjectStorage
     → ``mark_completed`` with storage URL.

Concurrency: pairs SEQUENTIAL within a job (avoid overwhelming WeasyPrint /
DB). Multiple jobs CAN run in parallel — each has its own session, its own
``UPDATE ... SET completed_count = completed_count + 1`` statement (atomic
at row level), и работают с разными ``job_id``-keyed storage objects.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from shared_models.orm import BundleStatus
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from report_service.bundles.auto_claim import (
    AutoClaimUnresolvableError,
    auto_derive_claim,
)
from report_service.bundles.bundling import (
    PairResult,
    build_consolidated_pdf,
    build_zip,
)
from report_service.bundles.data import (
    BundleOutputFormat,
    increment_completed,
    increment_failed,
    load_bundle_job,
    mark_completed,
    mark_failed,
    mark_running,
)
from report_service.bundles.storage import (
    content_type_for,
    get_bundle_storage,
    storage_key,
)
from report_service.relationship.models import ClaimedRelationship
from report_service.relationship.pipeline import generate_pdf_bytes_for_pair
from report_service.relationship.render import PdfRenderError

_LOG = logging.getLogger(__name__)


async def run_bundle_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    storage: Any | None = None,
    now_factory: Any | None = None,
) -> dict[str, Any]:
    """Execute one bundle job end-to-end.

    Args:
        session_factory: Async session factory (per-loop).
        job_id: UUID :class:`ReportBundleJob` row.
        storage: Optional ObjectStorage override (tests pass InMemoryStorage).
        now_factory: Optional callable returning ``dt.datetime`` (tests freeze).

    Returns:
        ``{"status", "completed", "failed"}`` summary.
    """
    now = now_factory or (lambda: dt.datetime.now(dt.UTC))
    actual_storage = storage or get_bundle_storage()

    # Phase 1 — lifecycle: queued → running.
    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        if job is None:
            _LOG.warning("bundle worker: job %s not found, skipping", job_id)
            return {"status": "missing", "completed": 0, "failed": 0}
        if job.status == BundleStatus.CANCELLED.value:
            _LOG.info("bundle worker: job %s cancelled before start, skipping", job_id)
            return {"status": "cancelled", "completed": 0, "failed": 0}
        await mark_running(session, job_id=job_id, now=now())
        await session.commit()
        # Snapshot inputs (avoid keeping ORM-row across long pair-loop).
        tree_id: uuid.UUID = job.tree_id
        relationship_pairs: list[dict[str, Any]] = list(job.relationship_pairs)
        output_format: str = job.output_format
        confidence_threshold: float | None = job.confidence_threshold

    # Phase 2 — per-pair generation (sequential).
    results: list[PairResult] = []
    completed = 0
    failed = 0

    for pair_index, raw_pair in enumerate(relationship_pairs):
        try:
            person_a_id = uuid.UUID(raw_pair["person_a_id"])
            person_b_id = uuid.UUID(raw_pair["person_b_id"])
        except (KeyError, TypeError, ValueError) as exc:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=f"Malformed pair entry: {exc}",
            )
            failed += 1
            continue

        if person_a_id == person_b_id:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message="person_a_id and person_b_id must differ.",
            )
            failed += 1
            continue

        claim_value = raw_pair.get("claimed_relationship")
        try:
            claim = await _resolve_claim(
                session_factory,
                tree_id=tree_id,
                person_a_id=person_a_id,
                person_b_id=person_b_id,
                claim_value=claim_value,
            )
        except AutoClaimUnresolvableError as exc:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=str(exc),
            )
            failed += 1
            continue
        except ValueError as exc:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=f"Invalid claimed_relationship value: {exc}",
            )
            failed += 1
            continue

        try:
            async with session_factory() as session:
                artifact = await generate_pdf_bytes_for_pair(
                    session,
                    tree_id=tree_id,
                    person_a_id=person_a_id,
                    person_b_id=person_b_id,
                    claim=claim,
                )
        except KeyError as exc:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=f"Tree/person not found: {exc}",
            )
            failed += 1
            continue
        except PdfRenderError as exc:
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=f"PDF render failed: {exc}",
            )
            failed += 1
            continue

        ctx = artifact.context
        if confidence_threshold is not None and ctx.confidence < confidence_threshold:
            # Soft-skip: caller asked for "above threshold only"; record as
            # supplementary failure (counts toward failed_count) so the bundle
            # surface is honest about what's missing.
            await _record_failure(
                session_factory,
                job_id=job_id,
                pair_index=pair_index,
                raw_pair=raw_pair,
                message=(
                    f"confidence {ctx.confidence:.2f} below threshold "
                    f"{confidence_threshold:.2f}; pair excluded from bundle"
                ),
            )
            failed += 1
            continue

        from report_service.relationship.render import render_html  # noqa: PLC0415

        results.append(
            PairResult(
                pair_index=pair_index,
                person_a_id=person_a_id,
                person_b_id=person_b_id,
                claim=claim.value,
                confidence=ctx.confidence,
                evidence_count=len(ctx.evidence),
                counter_evidence_count=len(ctx.counter_evidence),
                pdf_bytes=artifact.pdf_bytes,
                html=render_html(ctx),
            )
        )

        async with session_factory() as session:
            await increment_completed(session, job_id=job_id)
            await session.commit()
        completed += 1

    # Phase 3 — assembly + finalize.
    if not results:
        async with session_factory() as session:
            await mark_failed(session, job_id=job_id, now=now())
            await session.commit()
        _LOG.warning(
            "bundle %s: all %d pairs failed → status=failed",
            job_id,
            failed,
        )
        return {"status": "failed", "completed": 0, "failed": failed}

    generated_at = now()
    if output_format == BundleOutputFormat.CONSOLIDATED_PDF.value:
        try:
            blob = build_consolidated_pdf(
                results,
                job_id=job_id,
                tree_id=tree_id,
                generated_at=generated_at,
            )
        except PdfRenderError as exc:
            _LOG.warning(
                "bundle %s: consolidated PDF assembly failed (%s); falling back to ZIP-of-PDFs",
                job_id,
                exc,
            )
            blob = build_zip(
                results,
                job_id=job_id,
                tree_id=tree_id,
                generated_at=generated_at,
            )
            output_format = BundleOutputFormat.ZIP_OF_PDFS.value
    else:
        blob = build_zip(
            results,
            job_id=job_id,
            tree_id=tree_id,
            generated_at=generated_at,
        )

    key = storage_key(tree_id=tree_id, job_id=job_id, output_format=output_format)
    await actual_storage.put(key, blob, content_type=content_type_for(output_format))

    async with session_factory() as session:
        await mark_completed(
            session,
            job_id=job_id,
            storage_url=key,
            now=now(),
        )
        await session.commit()

    _LOG.info(
        "bundle %s: completed (%d pairs, %d failed, %d bytes, format=%s)",
        job_id,
        completed,
        failed,
        len(blob),
        output_format,
    )
    return {"status": "completed", "completed": completed, "failed": failed}


async def _resolve_claim(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
    claim_value: str | None,
) -> ClaimedRelationship:
    """Vendor passthrough or auto-derive."""
    if claim_value is not None:
        return ClaimedRelationship(claim_value)
    async with session_factory() as session:
        return await auto_derive_claim(
            session,
            tree_id=tree_id,
            person_a_id=person_a_id,
            person_b_id=person_b_id,
        )


async def _record_failure(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    job_id: uuid.UUID,
    pair_index: int,
    raw_pair: dict[str, Any],
    message: str,
) -> None:
    """Log to error_summary + increment failed_count в одной transaction."""
    entry = {
        "pair_index": pair_index,
        "person_a_id": raw_pair.get("person_a_id"),
        "person_b_id": raw_pair.get("person_b_id"),
        "message": message,
    }
    async with session_factory() as session:
        await increment_failed(session, job_id=job_id, error_entry=entry)
        await session.commit()


__all__ = ["run_bundle_job"]
