"""Phase 10.2b — image preprocessing для vision-extraction (см. ADR-0059 §10.2b).

Задача — привести user-uploaded image в форму, удобную и для Claude vision,
и для дальнейшего хранения в ``raw_response``:

1. **EXIF auto-orient.** Камеры пишут реальную ориентацию в EXIF,
   а пиксели оставляют «как сенсор увидел». Без normalize'а Claude
   получает повёрнутый кадр и читает текст вверх ногами.
2. **Downscale > 2048 px.** Anthropic vision API считает image-tokens
   пропорционально размеру; ≥ 2048 px по большой стороне даёт
   diminishing returns (документация Anthropic vision). Sonnet 4.6
   на сканах метрик 1500–2000 px показывает квази-идентичный recall
   к 4000 px, при ~ 4× меньшей стоимости.
3. **Format whitelist.** Anthropic vision принимает только
   ``image/jpeg``, ``image/png``, ``image/gif``, ``image/webp``.
   Любой другой mime — 415 на API-уровне; здесь — :class:`UnsupportedImageFormatError`
   до отправки в LLM.

Pillow — единственная runtime-зависимость; добавлена в parser-service
deps (см. ``pyproject.toml``). Тесты используют синтезированные in-memory
images через ``PIL.Image.new``, никаких fixtures на диске.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from ai_layer.clients.anthropic_client import ImageInput
from PIL import Image, ImageOps, UnidentifiedImageError

# Anthropic vision поддерживает эти media types — список из публичной
# документации https://docs.anthropic.com/en/docs/build-with-claude/vision
SUPPORTED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    },
)

# Хард-лимит большей стороны: больше — пересжимаем. ADR-0059 §10.2b deltas.
MAX_DIMENSION_PX: int = 2048

# Pillow-имена форматов для save(); ключ — media type, value — Pillow
# format. ``image/jpg`` не существует как канонический mime, но мы его
# нормализуем в ``image/jpeg`` ниже на всякий случай.
_PILLOW_FORMAT_BY_MEDIA: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/gif": "GIF",
    "image/webp": "WEBP",
}


class ImagePreprocessingError(RuntimeError):
    """Базовый класс ошибок этого модуля."""


class UnsupportedImageFormatError(ImagePreprocessingError):
    """media_type не в списке поддерживаемых Anthropic vision."""


class CorruptImageError(ImagePreprocessingError):
    """Pillow не смог открыть байты как image (битый файл / не image)."""


@dataclass(frozen=True)
class PreprocessedImage:
    """Результат :func:`preprocess_image`.

    Attributes:
        image_input: Готовый :class:`ImageInput` для Anthropic SDK
            (base64 + media_type).
        original_size_bytes: Исходный размер до пересжатия — для аудита /
            UI «оптимизировано c 5.2 МБ → 0.8 МБ».
        processed_size_bytes: Размер после пересжатия.
        original_dimensions: ``(width, height)`` исходного изображения.
        processed_dimensions: ``(width, height)`` финального.
        was_resized: True, если применили downscale.
        was_rotated: True, если EXIF orientation отличался от 1
            (т. е. пришлось повернуть пиксели).
    """

    image_input: ImageInput
    original_size_bytes: int
    processed_size_bytes: int
    original_dimensions: tuple[int, int]
    processed_dimensions: tuple[int, int]
    was_resized: bool
    was_rotated: bool


def _exif_orientation(img: Image.Image) -> int | None:
    """Прочитать EXIF orientation tag (0x0112).

    Pillow ≥ 9 предоставляет ``getexif()`` единым API; legacy ``_getexif``
    упасть может с AttributeError на не-jpeg image'ах. Возвращаем None
    для image'ов без EXIF — ``preprocess_image`` интерпретирует это как
    «поворот не нужен».
    """
    try:
        exif = img.getexif()
    except (AttributeError, OSError, ValueError):
        return None
    if not exif:
        return None
    raw = exif.get(0x0112)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def normalize_media_type(value: str) -> str:
    """Нормализовать media-type строку к каноническому виду.

    ``image/jpg`` → ``image/jpeg``, lowercase trim. Не угадываем — если
    после нормализации не в whitelist'е, caller получит 415.
    """
    cleaned = value.strip().lower()
    if cleaned == "image/jpg":
        return "image/jpeg"
    return cleaned


def preprocess_image(
    image_bytes: bytes,
    media_type: str,
    *,
    max_dimension: int = MAX_DIMENSION_PX,
) -> PreprocessedImage:
    """Подготовить image к vision-вызову.

    Pipeline:

    1. Validate media_type (нормализуем + whitelist).
    2. Open через Pillow (бросает :class:`CorruptImageError` если битый).
    3. ``ImageOps.exif_transpose`` — если EXIF orientation != 1, поворачиваем.
    4. Если max(width, height) > ``max_dimension`` — downscale пропорционально.
    5. Re-encode в исходный формат (jpeg quality=90, png — lossless).
    6. Base64-кодируем bytes для ``ImageInput``.

    Raises:
        UnsupportedImageFormatError: media_type не в whitelist'е.
        CorruptImageError: Pillow не смог открыть.
    """
    normalized_type = normalize_media_type(media_type)
    if normalized_type not in SUPPORTED_MEDIA_TYPES:
        msg = (
            f"Unsupported image media_type {media_type!r}; "
            f"Anthropic vision accepts {sorted(SUPPORTED_MEDIA_TYPES)}."
        )
        raise UnsupportedImageFormatError(msg)

    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Pillow ленивая — ``open`` не читает пиксели; ``load`` форсит чтение
        # и упадёт на битом файле здесь, а не позже на ``transpose``.
        img.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        msg = f"Cannot open image bytes (size={len(image_bytes)}): {exc}"
        raise CorruptImageError(msg) from exc

    original_size = (img.width, img.height)

    # EXIF orientation — определяем по тегу 0x0112; если отсутствует или
    # равен 1, поворот не нужен. ``ImageOps.exif_transpose`` всё равно
    # вызываем (он переносит остальные EXIF-нормализации), но ``was_rotated``
    # выставляем по реальному значению, а не по identity-проверке (Pillow
    # на разных версиях возвращает то новый, то тот же объект).
    exif_orientation = _exif_orientation(img)
    was_rotated = exif_orientation not in (None, 1)
    rotated = ImageOps.exif_transpose(img)
    img = rotated if rotated is not None else img

    # Downscale если нужно. ``thumbnail`` сохраняет aspect ratio и
    # модифицирует in-place; делает no-op если уже ≤ max_dimension.
    was_resized = max(img.width, img.height) > max_dimension
    if was_resized:
        img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)

    # Pillow для JPEG требует RGB (без alpha); для PNG — RGBA ok.
    pillow_format = _PILLOW_FORMAT_BY_MEDIA[normalized_type]
    if pillow_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
        # Конвертим к RGB; alpha-канал теряется, но JPEG всё равно его
        # не поддерживает.
        img = img.convert("RGB")

    out_buf = io.BytesIO()
    save_kwargs: dict[str, object] = {}
    if pillow_format == "JPEG":
        save_kwargs["quality"] = 90
        save_kwargs["optimize"] = True
    elif pillow_format == "PNG":
        save_kwargs["optimize"] = True
    elif pillow_format == "WEBP":
        save_kwargs["quality"] = 90
    img.save(out_buf, format=pillow_format, **save_kwargs)
    processed_bytes = out_buf.getvalue()

    return PreprocessedImage(
        image_input=ImageInput(
            data_b64=base64.b64encode(processed_bytes).decode("ascii"),
            media_type=normalized_type,
        ),
        original_size_bytes=len(image_bytes),
        processed_size_bytes=len(processed_bytes),
        original_dimensions=original_size,
        processed_dimensions=(img.width, img.height),
        was_resized=was_resized,
        was_rotated=was_rotated,
    )


__all__ = [
    "MAX_DIMENSION_PX",
    "SUPPORTED_MEDIA_TYPES",
    "CorruptImageError",
    "ImagePreprocessingError",
    "PreprocessedImage",
    "UnsupportedImageFormatError",
    "normalize_media_type",
    "preprocess_image",
]
