"""Bundle-format assembly: ZIP-of-PDFs / consolidated-PDF (Phase 24.4).

ZIP variant: каждый per-pair PDF под именем ``{pair_index:04d}.pdf`` плюс
``manifest.json`` с маппингом index → ``{person_a_id, person_b_id, claim,
confidence, evidence_count}``. PII safe — по ID'шникам, не имена.

Consolidated-PDF variant: WeasyPrint склеивает per-pair HTML'ы в один
документ с TOC + cover + непрерывной пагинацией. Реализуется одним
``HTML(string=combined_html).write_pdf()`` — WeasyPrint поддерживает
``page-break-before``/``after`` CSS, что мы и используем.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import uuid
import zipfile
from dataclasses import dataclass


@dataclass(frozen=True)
class PairResult:
    """Per-pair output for bundle assembly."""

    pair_index: int
    person_a_id: uuid.UUID
    person_b_id: uuid.UUID
    claim: str
    confidence: float
    evidence_count: int
    counter_evidence_count: int
    pdf_bytes: bytes
    html: str  # для consolidated-PDF mode


def build_zip(
    results: list[PairResult],
    *,
    job_id: uuid.UUID,
    tree_id: uuid.UUID,
    generated_at: dt.datetime,
) -> bytes:
    """ZIP-of-PDFs + manifest.json.

    Filenames: ``{pair_index:04d}.pdf`` (PII-safe — index, не имена).
    Manifest: маппинг index → metadata. Caller — endpoint /download.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest: list[dict[str, object]] = []
        for result in results:
            filename = f"{result.pair_index:04d}.pdf"
            zf.writestr(filename, result.pdf_bytes)
            manifest.append(
                {
                    "pair_index": result.pair_index,
                    "filename": filename,
                    "person_a_id": str(result.person_a_id),
                    "person_b_id": str(result.person_b_id),
                    "claimed_relationship": result.claim,
                    "confidence": result.confidence,
                    "evidence_count": result.evidence_count,
                    "counter_evidence_count": result.counter_evidence_count,
                }
            )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "job_id": str(job_id),
                    "tree_id": str(tree_id),
                    "generated_at": generated_at.isoformat(),
                    "pair_count": len(results),
                    "pairs": manifest,
                },
                indent=2,
                ensure_ascii=False,
            ),
        )
    return buf.getvalue()


def build_consolidated_pdf(
    results: list[PairResult],
    *,
    job_id: uuid.UUID,
    tree_id: uuid.UUID,
    generated_at: dt.datetime,
) -> bytes:
    """Один PDF: cover + TOC + per-pair sections с непрерывной пагинацией.

    Использует тот же WeasyPrint, что и 24.3. Per-pair HTML извлекаются
    из ``<body>`` через простой split — Jinja-шаблон 24.3 эмитит
    ``<body>...content...</body>``. Для cover/TOC встраиваем минимальный
    HTML preamble; собственный CSS — копия 24.3 base.html style block,
    плюс ``page-break-before: always`` на section.

    Raises:
        report_service.relationship.render.PdfRenderError: WeasyPrint
            native libs missing or assembly failure.
    """
    cover_and_toc = _build_cover_and_toc(
        results, job_id=job_id, tree_id=tree_id, generated_at=generated_at
    )
    sections: list[str] = []
    for result in results:
        body_inner = _extract_body(result.html)
        sections.append(
            f'<section class="bundle-section" id="pair-{result.pair_index:04d}">'
            f"{body_inner}</section>"
        )
    combined_html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Relationship Report Bundle</title>"
        f"{_BUNDLE_STYLE}</head><body>" + cover_and_toc + "".join(sections) + "</body></html>"
    )

    # Lazy import + identical error mapping as render.render_pdf — keeps
    # the bundle path's failure mode aligned with 24.3 (caller maps to
    # job-level failure / 503 in API).
    from report_service.relationship.render import PdfRenderError  # noqa: PLC0415

    try:
        from weasyprint import HTML  # noqa: PLC0415
    except (ImportError, OSError) as exc:
        msg = f"WeasyPrint unavailable: {exc}"
        raise PdfRenderError(msg) from exc
    try:
        return bytes(HTML(string=combined_html).write_pdf())
    except Exception as exc:
        msg = f"WeasyPrint consolidated render failed: {exc}"
        raise PdfRenderError(msg) from exc


def _build_cover_and_toc(
    results: list[PairResult],
    *,
    job_id: uuid.UUID,
    tree_id: uuid.UUID,
    generated_at: dt.datetime,
) -> str:
    rows = "".join(
        f"<li>#{r.pair_index + 1:04d}: {r.person_a_id} ⟷ {r.person_b_id} "
        f"({r.claim}, confidence {r.confidence:.2f})</li>"
        for r in results
    )
    return (
        "<section class='cover'>"
        "<h1>Relationship Report Bundle</h1>"
        f"<p><strong>Tree:</strong> {tree_id}</p>"
        f"<p><strong>Job:</strong> {job_id}</p>"
        f"<p><strong>Generated:</strong> {generated_at.isoformat()}</p>"
        f"<p><strong>Pairs included:</strong> {len(results)}</p>"
        "<h2>Table of contents</h2>"
        f"<ol>{rows}</ol>"
        "</section>"
    )


def _extract_body(html: str) -> str:
    """Извлечь содержимое ``<body>`` без обёртки.

    Если найти не удалось (что не должно случаться для 24.3-render), возвращаем
    оригинал — fail-open чтобы хотя бы что-то отрендерилось.
    """
    lower = html.lower()
    start = lower.find("<body")
    if start == -1:
        return html
    body_open_end = html.find(">", start)
    if body_open_end == -1:
        return html
    body_close = lower.rfind("</body>")
    if body_close == -1:
        return html[body_open_end + 1 :]
    return html[body_open_end + 1 : body_close]


_BUNDLE_STYLE: str = """<style>
@page {
  size: A4;
  margin: 24mm 18mm;
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font-size: 9pt;
    color: #555;
  }
}
body {
  font-family: "Times New Roman", Georgia, serif;
  font-size: 11pt;
  color: #111;
  line-height: 1.45;
}
.cover { page-break-after: always; padding-top: 30mm; }
.cover h1 { font-size: 22pt; margin: 0 0 4mm 0; font-weight: normal; }
.cover h2 { font-size: 14pt; margin: 8mm 0 2mm 0; }
.bundle-section { page-break-before: always; }
</style>"""


__all__ = [
    "PairResult",
    "build_consolidated_pdf",
    "build_zip",
]
