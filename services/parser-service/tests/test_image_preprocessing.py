"""Phase 10.2b — unit tests для image preprocessing pipeline.

Не требует Postgres / Anthropic — pure-Python tests на синтезированных
in-memory images через ``PIL.Image.new``. Маркеры ``db``/``integration``
не нужны; быстрый цикл.
"""

from __future__ import annotations

import base64
import io

import pytest
from parser_service.services.image_preprocessing import (
    MAX_DIMENSION_PX,
    SUPPORTED_MEDIA_TYPES,
    CorruptImageError,
    UnsupportedImageFormatError,
    normalize_media_type,
    preprocess_image,
)
from PIL import Image


def _png_bytes(width: int, height: int, *, mode: str = "RGB") -> bytes:
    """Сгенерировать тестовый PNG нужного размера."""
    img = Image.new(mode, (width, height), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Format whitelist.
# ---------------------------------------------------------------------------


def test_normalize_media_type_handles_jpg_synonym() -> None:
    assert normalize_media_type("image/jpg") == "image/jpeg"
    assert normalize_media_type("IMAGE/JPEG") == "image/jpeg"


def test_supported_media_types_match_anthropic_docs() -> None:
    """Sanity: Anthropic accepts jpeg/png/gif/webp на 2026-04 spec'е."""
    assert (
        frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"}) == SUPPORTED_MEDIA_TYPES
    )


def test_unsupported_format_raises() -> None:
    with pytest.raises(UnsupportedImageFormatError):
        preprocess_image(b"\x00" * 16, "image/tiff")


# ---------------------------------------------------------------------------
# Corrupt input.
# ---------------------------------------------------------------------------


def test_corrupt_bytes_raise_corrupt_image_error() -> None:
    with pytest.raises(CorruptImageError):
        preprocess_image(b"not an image at all", "image/png")


# ---------------------------------------------------------------------------
# Resize behavior.
# ---------------------------------------------------------------------------


def test_no_resize_for_small_image() -> None:
    raw = _png_bytes(800, 600)
    out = preprocess_image(raw, "image/png")
    assert out.was_resized is False
    assert out.processed_dimensions == (800, 600)


def test_resize_when_max_dimension_exceeded() -> None:
    """3000×2000 → ресайзим, large side ≤ 2048."""
    raw = _png_bytes(3000, 2000)
    out = preprocess_image(raw, "image/png")
    assert out.was_resized is True
    assert max(out.processed_dimensions) <= MAX_DIMENSION_PX
    # Aspect ratio сохранён.
    aspect_orig = 3000 / 2000
    aspect_new = out.processed_dimensions[0] / out.processed_dimensions[1]
    assert abs(aspect_orig - aspect_new) < 0.01


def test_resize_threshold_is_inclusive() -> None:
    """Картинка ровно 2048 — не ресайзится; 2049 — ресайзится."""
    out_at_limit = preprocess_image(_png_bytes(2048, 2048), "image/png")
    assert out_at_limit.was_resized is False
    out_over = preprocess_image(_png_bytes(2049, 2048), "image/png")
    assert out_over.was_resized is True


# ---------------------------------------------------------------------------
# JPEG / RGBA conversion.
# ---------------------------------------------------------------------------


def test_rgba_png_input_with_jpeg_output_drops_alpha() -> None:
    """Если caller специфит media_type=image/jpeg, RGBA → RGB конверт."""
    raw = _png_bytes(400, 300, mode="RGBA")
    out = preprocess_image(raw, "image/jpeg")
    # processed_size_bytes ≠ original — пересжатие в JPEG в любом случае.
    assert out.processed_size_bytes > 0
    assert out.image_input.media_type == "image/jpeg"
    # Распарсим результат — должен быть валидный JPEG-RGB.
    decoded = base64.b64decode(out.image_input.data_b64)
    parsed = Image.open(io.BytesIO(decoded))
    assert parsed.mode == "RGB"


# ---------------------------------------------------------------------------
# Output shape.
# ---------------------------------------------------------------------------


def test_image_input_is_base64_encoded() -> None:
    raw = _jpeg_bytes(100, 100)
    out = preprocess_image(raw, "image/jpeg")
    decoded = base64.b64decode(out.image_input.data_b64)
    # decoded bytes — валидный JPEG header.
    assert decoded[:3] == b"\xff\xd8\xff"


def test_metadata_fields_populated() -> None:
    raw = _png_bytes(1024, 768)
    out = preprocess_image(raw, "image/png")
    assert out.original_size_bytes == len(raw)
    assert out.original_dimensions == (1024, 768)
    assert out.processed_size_bytes > 0
