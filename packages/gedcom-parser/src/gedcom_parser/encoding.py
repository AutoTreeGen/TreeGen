"""Определение и декодирование кодировки GEDCOM-файла.

Алгоритм определения (в порядке убывания приоритета):

1. **BOM** — Byte Order Mark в начале файла:
   - ``EF BB BF`` → UTF-8
   - ``FF FE`` → UTF-16-LE
   - ``FE FF`` → UTF-16-BE
2. **HEAD CHAR** — заявленная кодировка в записи ``1 CHAR <name>`` HEAD-блока.
   Маппится на каноническое имя Python-кодека через ``_CHAR_TAG_MAPPING``.
3. **Эвристика** — если ничего из выше не применимо:
   - ASCII (только < 0x80) → ASCII
   - валидный UTF-8 → UTF-8
   - иначе → CP1251 (низкая уверенность; даём пользователю возможность переопределить)

Поддерживаемые кодировки для декодирования: всё, что понимает ``bytes.decode()``,
плюс особый случай **ANSEL** — он детектится, но нативного декодера в Python нет.
В Итерации 1.1 ANSEL → fallback на ``latin1`` с ``UserWarning``. Полноценный
ANSEL-декодер — отдельная задача (см. ROADMAP §5.1.2).
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

from gedcom_parser.exceptions import GedcomEncodingError, GedcomEncodingWarning
from gedcom_parser.models import EncodingInfo

# -----------------------------------------------------------------------------
# Константы
# -----------------------------------------------------------------------------

# BOM-сигнатуры. Порядок важен: UTF-8 BOM проверяется первым,
# чтобы не спутать с UTF-16 (хотя коллизии тут нет, но порядок логичный).
_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"

# Сам символ BOM (U+FEFF) после декодирования. Объявляем через chr(),
# чтобы исходник не содержал невидимый символ — на это ругаются линтеры
# (ruff RUF001) и редакторы.
_BOM_CHAR = chr(0xFEFF)

# Сколько байт в начале файла читать для эвристики и поиска HEAD CHAR.
# 4 KB достаточно для любого реального HEAD-блока.
_DETECTION_SAMPLE_SIZE = 4096

# Регулярка для поиска `1 CHAR <name>` в HEAD-блоке.
# Допускаем как пробелы, так и табы; имя кодировки — non-whitespace.
_HEAD_CHAR_RE = re.compile(r"^[ \t]*1[ \t]+CHAR[ \t]+(\S+)", re.MULTILINE)

# Маппинг значений `1 CHAR ...` на канонические имена Python-кодеков.
# Спецификация GEDCOM 5.5.1/5.5.5 формально допускает только UTF-8/ASCII/ANSEL,
# но реальные файлы из разных тулов используют десяток вариантов.
_CHAR_TAG_MAPPING: dict[str, str] = {
    # UTF-8 / ASCII
    "UTF-8": "UTF-8",
    "UTF8": "UTF-8",
    "ASCII": "ASCII",
    "US-ASCII": "ASCII",
    # ANSEL — особый случай, реального декодера в Python нет.
    "ANSEL": "ANSEL",
    # ANSI / Windows
    "ANSI": "CP1252",
    "WINDOWS-1252": "CP1252",
    "CP1252": "CP1252",
    "WINDOWS-1251": "CP1251",
    "CP1251": "CP1251",
    "1251": "CP1251",
    # IBM PC
    "IBMPC": "CP437",
    "IBM": "CP437",
    "CP437": "CP437",
    "CP866": "CP866",
    # Macintosh
    "MACINTOSH": "MAC-ROMAN",
    "MACROMAN": "MAC-ROMAN",
    # UTF-16
    "UTF-16": "UTF-16",
    "UTF16": "UTF-16",
    "UNICODE": "UTF-16",
}


# -----------------------------------------------------------------------------
# Публичный API
# -----------------------------------------------------------------------------


def detect_encoding(raw: bytes) -> EncodingInfo:
    """Определить кодировку GEDCOM-байт по BOM, HEAD CHAR или эвристике.

    Args:
        raw: Содержимое GEDCOM-файла (или его начало; достаточно ~4 KB).

    Returns:
        ``EncodingInfo`` с каноническим именем кодировки и оценкой уверенности.
    """
    # ---- 1. BOM ----------------------------------------------------------
    if raw.startswith(_BOM_UTF8):
        return EncodingInfo(name="UTF-8", confidence=1.0, method="bom")
    if raw.startswith(_BOM_UTF16_LE):
        return EncodingInfo(name="UTF-16-LE", confidence=1.0, method="bom")
    if raw.startswith(_BOM_UTF16_BE):
        return EncodingInfo(name="UTF-16-BE", confidence=1.0, method="bom")

    # ---- 2. HEAD CHAR ----------------------------------------------------
    # Читаем первые ~4 KB как latin1 (1 байт = 1 символ, не падает ни на чём)
    # и ищем `1 CHAR <name>`. Этот блок всегда в начале и в ASCII-подмножестве.
    sample = raw[:_DETECTION_SAMPLE_SIZE].decode("latin1", errors="replace")
    match = _HEAD_CHAR_RE.search(sample)
    if match is not None:
        char_value = match.group(1)
        canonical = _CHAR_TAG_MAPPING.get(char_value.upper(), char_value)
        return EncodingInfo(
            name=canonical,
            confidence=0.95,
            method="head_char",
            head_char_raw=char_value,
        )

    # ---- 3. Эвристика ----------------------------------------------------
    if _is_pure_ascii(raw):
        return EncodingInfo(name="ASCII", confidence=0.9, method="heuristic")
    if _is_valid_utf8(raw):
        return EncodingInfo(name="UTF-8", confidence=0.85, method="heuristic")
    # Низкая уверенность — пользователю стоит переопределить вручную, если важно.
    return EncodingInfo(name="CP1251", confidence=0.3, method="heuristic")


def decode_gedcom(raw: bytes) -> tuple[str, EncodingInfo]:
    """Декодировать GEDCOM-байты в строку, автоматически определив кодировку.

    Args:
        raw: Содержимое GEDCOM-файла.

    Returns:
        Кортеж ``(text, info)``: декодированный текст и информация о кодировке.

    Raises:
        GedcomEncodingError: Если кодировка определена, но Python-кодек её не понимает.
    """
    info = detect_encoding(raw)
    text = _decode_with_info(raw, info)
    # Снимаем BOM (U+FEFF), если он остался после декодирования.
    if text.startswith(_BOM_CHAR):
        text = text[1:]
    return text, info


def decode_gedcom_file(path: Path) -> tuple[str, EncodingInfo]:
    """Прочитать файл и декодировать его как GEDCOM."""
    raw = Path(path).read_bytes()
    return decode_gedcom(raw)


# -----------------------------------------------------------------------------
# Внутренние утилиты
# -----------------------------------------------------------------------------


def _is_pure_ascii(raw: bytes) -> bool:
    """True, если в байтах нет ничего за пределами ASCII."""
    return all(b < 0x80 for b in raw[:_DETECTION_SAMPLE_SIZE])


def _is_valid_utf8(raw: bytes) -> bool:
    """True, если первые ~4 KB декодируются как валидный UTF-8."""
    try:
        raw[:_DETECTION_SAMPLE_SIZE].decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _decode_with_info(raw: bytes, info: EncodingInfo) -> str:
    """Декодировать байты согласно ``EncodingInfo``.

    Особый случай: ANSEL не имеет нативного Python-кодека. В Итерации 1.1
    выдаётся ``UserWarning`` и используется fallback на latin1. Полноценный
    декодер ANSEL — задача следующих итераций.
    """
    if info.name == "ANSEL":
        warnings.warn(
            "ANSEL encoding detected but ANSEL decoder is not yet implemented. "
            "Falling back to latin1 — non-ASCII characters will be incorrect. "
            "See ROADMAP.md §5.1.2.",
            GedcomEncodingWarning,
            stacklevel=3,
        )
        return raw.decode("latin1", errors="replace")

    try:
        return raw.decode(info.name, errors="replace")
    except LookupError as exc:
        msg = f"Unknown encoding: {info.name!r} (declared as {info.head_char_raw!r})"
        raise GedcomEncodingError(msg) from exc
