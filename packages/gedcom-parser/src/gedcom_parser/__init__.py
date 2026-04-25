"""GEDCOM 5.5.5 парсер: декодирование, лексинг, AST.

Высокоуровневое API:

    >>> from gedcom_parser import parse_file
    >>> records, encoding = parse_file("tree.ged")
    >>> records[0].tag
    'HEAD'

Низкоуровневое — :func:`iter_lines`, :func:`detect_encoding`,
:func:`decode_gedcom`. Модели — :class:`GedcomLine`, :class:`GedcomRecord`,
:class:`EncodingInfo`.
"""

from gedcom_parser.encoding import decode_gedcom, decode_gedcom_file, detect_encoding
from gedcom_parser.exceptions import (
    GedcomEncodingError,
    GedcomError,
    GedcomLexerError,
    GedcomParseError,
)
from gedcom_parser.lexer import iter_lines
from gedcom_parser.models import EncodingInfo, GedcomLine, GedcomRecord
from gedcom_parser.parser import parse_bytes, parse_file, parse_records, parse_text

__version__ = "0.1.0"

__all__ = [
    "EncodingInfo",
    "GedcomEncodingError",
    "GedcomError",
    "GedcomLexerError",
    "GedcomLine",
    "GedcomParseError",
    "GedcomRecord",
    "__version__",
    "decode_gedcom",
    "decode_gedcom_file",
    "detect_encoding",
    "iter_lines",
    "parse_bytes",
    "parse_file",
    "parse_records",
    "parse_text",
]
