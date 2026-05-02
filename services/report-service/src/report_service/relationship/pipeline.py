"""Single-pair report pipeline — context assembly + HTML + PDF in one call.

Phase 24.4 carve-out: extracted from
``report_service.api.relationship.generate_relationship_report`` so that the
sync endpoint AND the bulk-bundle worker (Phase 24.4) can share one
implementation. Anti-fork guarantee per ADR-0078: this is the *only*
``def`` that walks pair → PDF; both call-sites import it.

Storage / signed-URL handling stays in the call-site — different layouts:

* Sync endpoint: ``relationship-reports/{tree_id}/{report_id}.pdf`` (one blob).
* Bundle worker: ``relationship-bundles/{job_id}/individual/{idx}.pdf``
  during assembly, then a single ZIP/PDF blob keyed by ``job_id``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from report_service.relationship.data import build_report_context
from report_service.relationship.models import (
    ClaimedRelationship,
    RelationshipReportContext,
    ReportLocale,
    ReportTitleStyle,
)
from report_service.relationship.render import render_html, render_pdf

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class RelationshipReportArtifact:
    """Output of one pair → PDF render.

    ``context`` is kept for callers that want metadata (confidence,
    evidence_count, counter_evidence_count) without re-computing.
    """

    context: RelationshipReportContext
    pdf_bytes: bytes


async def generate_pdf_bytes_for_pair(
    session: AsyncSession,
    *,
    tree_id: uuid.UUID,
    person_a_id: uuid.UUID,
    person_b_id: uuid.UUID,
    claim: ClaimedRelationship,
    locale: ReportLocale = "en",
    title_style: ReportTitleStyle = "formal",
    include_dna_evidence: bool = True,
    include_archive_evidence: bool = True,
    include_hypothesis_flags: bool = True,
    researcher_name: str | None = None,
) -> RelationshipReportArtifact:
    """Single source of truth для pair → PDF.

    Raises:
        KeyError: tree / person not found (caller maps to 404).
        report_service.relationship.render.PdfRenderError: WeasyPrint native
            libs missing or render failure (caller maps to 503).

    Storage upload + signed-URL is the caller's responsibility — see module
    docstring.
    """
    context = await build_report_context(
        session,
        tree_id=tree_id,
        person_a_id=person_a_id,
        person_b_id=person_b_id,
        claim=claim,
        locale=locale,
        title_style=title_style,
        include_dna_evidence=include_dna_evidence,
        include_archive_evidence=include_archive_evidence,
        include_hypothesis_flags=include_hypothesis_flags,
        researcher_name=researcher_name,
    )
    html = render_html(context)
    pdf_bytes = render_pdf(html)
    return RelationshipReportArtifact(context=context, pdf_bytes=pdf_bytes)


__all__ = ["RelationshipReportArtifact", "generate_pdf_bytes_for_pair"]
