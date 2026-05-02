"""Jinja2 рендер HTML + WeasyPrint конверт в PDF.

Шаблоны живут в ``services/report-service/templates/relationship/`` —
вне ``src/`` дерева. Каталог обнаруживается через walking up от модуля.

PDF:
    weasyprint требует системных libpango / libgdk-pixbuf. На Windows-dev
    могут отсутствовать; в этом случае :func:`render_pdf` raise'нет
    ``PdfRenderError``, а call-site (тесты skip, endpoint → 503) graceful'ит.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from report_service.relationship.citations import format_chicago
from report_service.relationship.locale import (
    claim_label,
    confidence_method_label,
    evidence_kind_label,
    report_title,
    sex_label,
    t,
)
from report_service.relationship.models import (
    CitationRef,
    EvidencePiece,
    RelationshipReportContext,
)


class PdfRenderError(RuntimeError):
    """WeasyPrint не смог сконвертировать HTML — нет native libs либо bug."""


def _templates_dir() -> Path:
    """Корень templates/relationship относительно файла модуля.

    Поднимаемся от ``…/src/report_service/relationship/render.py`` до
    ``…/services/report-service/`` и берём оттуда ``templates``.
    """
    here = Path(__file__).resolve()
    # parents[3] = services/report-service
    candidate = here.parents[3] / "templates" / "relationship"
    if not candidate.is_dir():
        msg = f"Templates dir not found: {candidate}"
        raise FileNotFoundError(msg)
    return candidate


def _build_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_templates_dir())),
        autoescape=select_autoescape(["html", "xml"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals["format_chicago"] = format_chicago
    env.globals["t"] = t
    env.globals["claim_label"] = claim_label
    env.globals["confidence_method_label"] = confidence_method_label
    env.globals["evidence_kind_label"] = evidence_kind_label
    env.globals["sex_label"] = sex_label
    env.globals["report_title"] = report_title
    env.filters["fmt_date"] = _fmt_date
    return env


def _fmt_date(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return value.strftime("%Y-%m-%d")


def _build_footnotes(
    context: RelationshipReportContext,
) -> tuple[list[CitationRef], dict[str, int]]:
    """Линейная нумерация footnote'ов.

    Один и тот же ``(source_id, citation_id)`` получает один номер.
    Возвращает (ordered_list, "src|cit"-keyed_lookup).
    """
    seen: dict[tuple[str, str], int] = {}
    ordered: list[CitationRef] = []

    def _ingest(cit: CitationRef) -> None:
        key = (str(cit.source_id), str(cit.citation_id))
        if key in seen:
            return
        seen[key] = len(ordered) + 1
        ordered.append(cit)

    def _ingest_pieces(pieces: list[EvidencePiece]) -> None:
        for p in pieces:
            for c in p.citations:
                _ingest(c)

    _ingest_pieces(context.evidence)
    _ingest_pieces(context.counter_evidence)

    flat_index = {f"{src}|{cit}": idx for (src, cit), idx in seen.items()}
    return ordered, flat_index


def render_html(context: RelationshipReportContext) -> str:
    """Рендер HTML без PDF-конверта. Вход ``RelationshipReportContext`` → utf-8 string."""
    env = _build_env()
    template = env.get_template("relationship.html")
    footnotes, footnote_index = _build_footnotes(context)
    rendered: str = template.render(
        ctx=context,
        footnotes=footnotes,
        footnote_index=footnote_index,
    )
    return rendered


def render_pdf(html: str) -> bytes:
    """HTML → PDF bytes через WeasyPrint.

    Raises:
        PdfRenderError: если WeasyPrint native libs недоступны / падает.
    """
    try:
        from weasyprint import HTML  # noqa: PLC0415  — lazy import
    except (ImportError, OSError) as exc:
        msg = f"WeasyPrint unavailable: {exc}"
        raise PdfRenderError(msg) from exc
    try:
        return bytes(HTML(string=html).write_pdf())
    except Exception as exc:
        msg = f"WeasyPrint render failed: {exc}"
        raise PdfRenderError(msg) from exc


__all__ = ["PdfRenderError", "render_html", "render_pdf"]
