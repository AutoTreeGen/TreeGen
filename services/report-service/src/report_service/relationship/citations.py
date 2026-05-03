"""Chicago-style footnote formatter.

Адаптация из ``parser_service.court_ready.citations`` (Phase 15.6) —
скопирована (а не импортирована), чтобы report-service не зависел
runtime от parser-service. Полное BCG (Board for Certification of
Genealogists) Citation Manual покрытие — отдельный ADR; тут MVP
покрывает книги, архивные документы, веб-источники.

См. ADR-0073.
"""

from __future__ import annotations

from report_service.relationship.models import CitationRef


def format_chicago(citation: CitationRef) -> str:
    """Сборка одной footnote-строки Chicago-style.

    Возвращает plain-text без HTML — рендер Jinja-шаблона дальше
    escape'ит. Совместима с court_ready format_chicago по форме вывода
    чтобы snapshot-тесты обоих сервисов могли диффатся.
    """
    parts: list[str] = []

    if citation.author:
        parts.append(f"{citation.author},")
    parts.append(f"“{citation.source_title}”")  # «curly» quotes

    pub_segment = _publication_segment(citation)
    if pub_segment:
        parts.append(f"({pub_segment})")

    if citation.repository:
        parts.append(f"— {citation.repository}")

    if citation.page_or_section:
        parts.append(f", {citation.page_or_section}")

    if citation.url:
        parts.append(f", {citation.url}")

    base = " ".join(parts).rstrip(",") + "."

    quality_marker = _quality_marker(citation)
    if quality_marker:
        base += f" [{quality_marker}]"

    quoted = _quoted_excerpt(citation)
    if quoted:
        base += f" {quoted}"

    return base


def _publication_segment(citation: CitationRef) -> str | None:
    """`publisher, year` либо просто `year` либо None."""
    bits: list[str] = []
    if citation.publication:
        bits.append(citation.publication)
    if citation.publication_date:
        bits.append(str(citation.publication_date.year))
    if not bits:
        return None
    return ", ".join(bits)


def _quality_marker(citation: CitationRef) -> str | None:
    """`QUAY 3` → ``primary``, 2 → ``secondary``, 1 → ``questionable``, 0 → ``unreliable``."""
    if citation.quay_raw is None:
        return None
    mapping = {
        3: "primary source",
        2: "secondary source",
        1: "questionable",
        0: "unreliable",
    }
    return mapping.get(citation.quay_raw)


def _quoted_excerpt(citation: CitationRef) -> str | None:
    """Quoted text в кавычках, обрезка до 200 символов."""
    text = (citation.quoted_text or "").strip()
    if not text:
        return None
    if len(text) > 200:
        text = text[:197] + "..."
    return f"“{text}”"


__all__ = ["format_chicago"]
