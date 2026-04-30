"""Phase 10.2b — unit tests для extract_first_image_from_scanned_pdf.

Pure-python (без Postgres / Anthropic). Используем pypdf для синтеза
PDF с одним embedded JPEG image.
"""

from __future__ import annotations

import io

import pytest
from parser_service.services.ai_source_extraction import (
    AISourceExtractionError,
    extract_first_image_from_scanned_pdf,
)
from PIL import Image
from pypdf import PdfWriter


def _make_scanned_pdf_bytes(*, image_width: int = 400, image_height: int = 300) -> bytes:
    """Соорудить минимальный PDF с одним embedded image (scanned-like).

    Pillow умеет сохранять image как PDF напрямую — этот PDF содержит
    одну страницу с embedded image-XObject, что точно соответствует
    user-сценарию «scanned page → PDF».
    """
    img = Image.new("RGB", (image_width, image_height), color="lightgray")
    out_buf = io.BytesIO()
    img.save(out_buf, format="PDF", resolution=72.0)
    return out_buf.getvalue()


# ---------------------------------------------------------------------------
# Happy path.
# ---------------------------------------------------------------------------


def test_extracts_first_image_from_scanned_pdf() -> None:
    """PDF с embedded image → возвращаем bytes + распознаваемый media_type."""
    pdf = _make_scanned_pdf_bytes()
    result = extract_first_image_from_scanned_pdf(pdf)
    assert result.page_index == 0
    # Pillow's PDF writer выдаёт JPEG или PNG — оба валидны для нашего
    # vision pipeline'а; конкретный формат зависит от Pillow-версии.
    assert result.media_type in {"image/jpeg", "image/png"}
    assert len(result.image_bytes) > 0


# ---------------------------------------------------------------------------
# Failure modes.
# ---------------------------------------------------------------------------


def test_corrupt_pdf_raises() -> None:
    with pytest.raises(AISourceExtractionError):
        extract_first_image_from_scanned_pdf(b"not a pdf")


def test_pdf_without_images_raises() -> None:
    """Pure-text PDF без images → AISourceExtractionError.

    Без embedded image vision-fallback физически невозможен; caller
    должен показать понятную ошибку «vision не применим».
    """
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out_buf = io.BytesIO()
    writer.write(out_buf)

    with pytest.raises(AISourceExtractionError, match="no embedded images"):
        extract_first_image_from_scanned_pdf(out_buf.getvalue())
