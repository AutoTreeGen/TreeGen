"""Phase 10.2 — unit tests для pypdf-helper'а в ai_source_extraction service.

Покрывают:

* extract_text_from_pdf на изображении-only / пустом PDF → PoorPdfQualityError.
* extract_text_from_pdf на битом файле → AISourceExtractionError.

Success-path (PDF с текстом) проверяется в integration-тестах endpoint'а
``POST /sources/{id}/ai-extract`` через ``document_text``-override —
генерация валидного PDF с extractable text без reportlab непрактична.

Эти тесты не зависят от testcontainers — pypdf работает чистым in-memory.
"""

from __future__ import annotations

import io

import pytest
from parser_service.services.ai_source_extraction import (
    AISourceExtractionError,
    PoorPdfQualityError,
    extract_text_from_pdf,
)


def test_extract_text_from_blank_pdf_raises_poor_quality() -> None:
    """Pdf с пустой страницей — < 50 chars → PoorPdfQualityError."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)

    with pytest.raises(PoorPdfQualityError):
        extract_text_from_pdf(buf.getvalue())


def test_extract_text_from_corrupt_pdf_raises_ai_error() -> None:
    with pytest.raises(AISourceExtractionError):
        extract_text_from_pdf(b"not a pdf, just garbage bytes")
