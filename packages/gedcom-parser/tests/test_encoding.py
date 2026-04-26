"""Тесты определения и декодирования кодировок."""

from __future__ import annotations

import pytest
from gedcom_parser.encoding import decode_gedcom, detect_encoding
from gedcom_parser.exceptions import GedcomEncodingError

# -----------------------------------------------------------------------------
# BOM detection
# -----------------------------------------------------------------------------


class TestBOMDetection:
    def test_utf8_bom(self) -> None:
        info = detect_encoding(b"\xef\xbb\xbf0 HEAD\n")
        assert info.name == "UTF-8"
        assert info.method == "bom"
        assert info.confidence == 1.0

    def test_utf16_le_bom(self) -> None:
        info = detect_encoding(b"\xff\xfe0\x00 \x00H\x00")
        assert info.name == "UTF-16-LE"
        assert info.method == "bom"

    def test_utf16_be_bom(self) -> None:
        info = detect_encoding(b"\xfe\xff\x000\x00 \x00H")
        assert info.name == "UTF-16-BE"
        assert info.method == "bom"


# -----------------------------------------------------------------------------
# HEAD CHAR detection
# -----------------------------------------------------------------------------


def _make_head(char_value: str) -> bytes:
    """Минимальный HEAD-блок с указанным значением CHAR."""
    return f"0 HEAD\n1 CHAR {char_value}\n0 TRLR\n".encode("latin1")


class TestHeadCharDetection:
    def test_utf8(self) -> None:
        info = detect_encoding(_make_head("UTF-8"))
        assert info.name == "UTF-8"
        assert info.method == "head_char"
        assert info.head_char_raw == "UTF-8"

    def test_utf8_no_dash(self) -> None:
        info = detect_encoding(_make_head("UTF8"))
        assert info.name == "UTF-8"
        assert info.head_char_raw == "UTF8"

    def test_ansel(self) -> None:
        info = detect_encoding(_make_head("ANSEL"))
        assert info.name == "ANSEL"
        assert info.method == "head_char"

    def test_ansi_maps_to_cp1252(self) -> None:
        info = detect_encoding(_make_head("ANSI"))
        assert info.name == "CP1252"
        assert info.head_char_raw == "ANSI"

    def test_windows_1251(self) -> None:
        info = detect_encoding(_make_head("WINDOWS-1251"))
        assert info.name == "CP1251"

    def test_ibmpc_maps_to_cp437(self) -> None:
        info = detect_encoding(_make_head("IBMPC"))
        assert info.name == "CP437"

    def test_unknown_passthrough(self) -> None:
        # Неизвестная кодировка проходит как есть (с raw-значением).
        info = detect_encoding(_make_head("WEIRD-UNKNOWN-CODEC"))
        assert info.name == "WEIRD-UNKNOWN-CODEC"
        assert info.method == "head_char"


# -----------------------------------------------------------------------------
# Heuristic detection (no BOM, no HEAD CHAR)
# -----------------------------------------------------------------------------


class TestHeuristicDetection:
    def test_pure_ascii(self) -> None:
        info = detect_encoding(b"0 HEAD\n0 TRLR\n")
        assert info.name == "ASCII"
        assert info.method == "heuristic"

    def test_valid_utf8_with_non_ascii(self) -> None:
        # Без HEAD CHAR, но с не-ASCII в UTF-8.
        raw = "0 HEAD\n1 NOTE Привет мир\n0 TRLR\n".encode()
        info = detect_encoding(raw)
        assert info.name == "UTF-8"
        assert info.method == "heuristic"

    def test_invalid_utf8_falls_back_to_cp1251(self) -> None:
        # Чисто CP1251 байты, без CHAR — попадает в fallback.
        raw = "0 HEAD\n1 NOTE Привет\n0 TRLR\n".encode("cp1251")
        info = detect_encoding(raw)
        assert info.name == "CP1251"
        # Fallback имеет низкую уверенность.
        assert info.confidence < 0.5


# -----------------------------------------------------------------------------
# decode_gedcom: end-to-end
# -----------------------------------------------------------------------------


class TestDecodeGedcom:
    def test_decode_utf8_strips_bom(self) -> None:
        text, info = decode_gedcom(b"\xef\xbb\xbf0 HEAD\n0 TRLR\n")
        assert info.name == "UTF-8"
        assert text == "0 HEAD\n0 TRLR\n"
        assert not text.startswith(chr(0xFEFF))

    def test_decode_cyrillic_in_cp1251(self) -> None:
        raw = "0 HEAD\n1 CHAR WINDOWS-1251\n1 NOTE Привет\n0 TRLR\n".encode("cp1251")
        text, info = decode_gedcom(raw)
        assert info.name == "CP1251"
        assert "Привет" in text

    def test_decode_ansel_warns_and_falls_back(self) -> None:
        # ANSEL пока не реализован — должен быть warning + fallback на latin1.
        raw = _make_head("ANSEL")
        with pytest.warns(UserWarning, match="ANSEL"):
            text, info = decode_gedcom(raw)
        assert info.name == "ANSEL"
        # Результат — какая-то строка (не ошибка).
        assert isinstance(text, str)
        assert "HEAD" in text

    def test_decode_unknown_codec_raises(self) -> None:
        # Неизвестный codec, который Python не понимает.
        raw = _make_head("CODEC-DOES-NOT-EXIST-12345")
        with pytest.raises(GedcomEncodingError):
            decode_gedcom(raw)
