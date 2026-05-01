"""Jinja2 рендер HTML + WeasyPrint конверт в PDF.

Шаблоны живут в ``services/parser-service/templates/court_ready/`` —
вне ``src/`` дерева, рядом с серсивом. Каталог обнаруживается через
walking up от модуля до тех пор пока не найдётся ``templates/court_ready``.

PDF:
    weasyprint требует системных libpango / libgdk-pixbuf. На CI
    ubuntu-latest они есть; на Windows-dev могут отсутствовать. В этом
    случае :func:`render_pdf` raise'нет ``PdfRenderError``, а call-site
    решает что делать (тесты skip, endpoint → 503).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from parser_service.court_ready.citations import format_chicago
from parser_service.court_ready.locale import (
    confidence_method_label,
    event_label,
    evidence_kind_label,
    negative_kind_label,
    relation_label,
    scope_label,
    sex_label,
    t,
)
from parser_service.court_ready.models import (
    CitationRef,
    ReportContext,
    ReportScope,
)


class PdfRenderError(RuntimeError):
    """WeasyPrint не смог сконвертировать HTML — нет native libs либо bug."""


_SCOPE_TO_TEMPLATE: Final[dict[ReportScope, str]] = {
    "person": "person.html",
    "family": "family.html",
    "ancestry_to_gen": "ancestry.html",
}


def _templates_dir() -> Path:
    """Корень templates/court_ready относительно файла модуля.

    Поднимаемся от ``…/src/parser_service/court_ready/render.py`` до
    ``…/services/parser-service/`` и берём оттуда ``templates``.
    """
    here = Path(__file__).resolve()
    # ../../../../ = src/parser_service/court_ready -> services/parser-service
    candidate = here.parents[3] / "templates" / "court_ready"
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
    env.globals["event_label"] = event_label
    env.globals["sex_label"] = sex_label
    env.globals["relation_label"] = relation_label
    env.globals["evidence_kind_label"] = evidence_kind_label
    env.globals["confidence_method_label"] = confidence_method_label
    env.globals["scope_label"] = scope_label
    env.globals["negative_kind_label"] = negative_kind_label
    env.filters["fmt_date"] = _fmt_date
    return env


def _fmt_date(value: dt.date | dt.datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return value.strftime("%Y-%m-%d")


def _build_footnotes(
    context: ReportContext,
) -> tuple[list[CitationRef], dict[tuple[str, str], int]]:
    """Линейная нумерация footnote'ов.

    Один и тот же ``(source_id, citation_id)`` получает один номер. Возвращает
    (ordered_list, lookup_map). Lookup map используется в шаблоне:
    ``footnote_index[(citation.source_id|string, citation.citation_id|string)]``.
    """
    seen: dict[tuple[str, str], int] = {}
    ordered: list[CitationRef] = []

    def _ingest(cit: CitationRef) -> None:
        key = (str(cit.source_id), str(cit.citation_id))
        if key in seen:
            return
        seen[key] = len(ordered) + 1
        ordered.append(cit)

    if context.subject.birth:
        for c in context.subject.birth.citations:
            _ingest(c)
    if context.subject.death:
        for c in context.subject.death.citations:
            _ingest(c)
    for ev in context.other_events:
        for c in ev.citations:
            _ingest(c)
    for rel in context.relationships:
        for c in rel.citations:
            _ingest(c)
    for anc in context.ancestry:
        if anc.birth:
            for c in anc.birth.citations:
                _ingest(c)
        if anc.death:
            for c in anc.death.citations:
                _ingest(c)
    return ordered, seen


def render_html(context: ReportContext) -> str:
    """Рендер HTML без PDF-конверта. Вход ``ReportContext``, выход — utf-8 string.

    Используется напрямую тестами (snapshot) и косвенно :func:`render_pdf`.
    """
    env = _build_env()
    template_name = _SCOPE_TO_TEMPLATE[context.scope]
    template = env.get_template(template_name)
    footnotes, footnote_index = _build_footnotes(context)
    # Jinja-friendly map: tuple-keys плохо рендерятся; сериализуем в "{src}|{cit}".
    flat_index = {f"{src}|{cit}": idx for (src, cit), idx in footnote_index.items()}
    rendered: str = template.render(
        ctx=context,
        footnotes=footnotes,
        footnote_index=flat_index,
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
