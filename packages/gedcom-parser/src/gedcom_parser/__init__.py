"""GEDCOM 5.5.5 парсер: декодирование, лексинг, AST, семантика.

Высокоуровневое API:

    >>> from gedcom_parser import parse_document_file
    >>> doc = parse_document_file("tree.ged")
    >>> len(doc.persons), len(doc.families)
    (3, 1)
    >>> doc.verify_references()
    []

Низкоуровневое — :func:`iter_lines`, :func:`detect_encoding`,
:func:`decode_gedcom`, :func:`parse_records`. AST-модели —
:class:`GedcomLine`, :class:`GedcomRecord`. Семантические сущности —
:class:`Person`, :class:`Family`, :class:`Event`, :class:`Name` и т.д.
"""

from gedcom_parser.dates import (
    Calendar,
    ParsedDate,
    Qualifier,
    french_republican_to_gregorian,
    hebrew_to_gregorian,
    julian_to_gregorian,
    parse_gedcom_date,
)
from gedcom_parser.document import BrokenRef, GedcomDocument
from gedcom_parser.encoding import decode_gedcom, decode_gedcom_file, detect_encoding
from gedcom_parser.entities import (
    Event,
    Family,
    Header,
    MultimediaObject,
    Name,
    Note,
    Person,
    Repository,
    Source,
    Submitter,
)
from gedcom_parser.exceptions import (
    GedcomDateParseError,
    GedcomDateWarning,
    GedcomEncodingError,
    GedcomEncodingWarning,
    GedcomError,
    GedcomLenientWarning,
    GedcomLexerError,
    GedcomParseError,
    GedcomReferenceWarning,
    GedcomWarning,
)
from gedcom_parser.names import (
    NameVariant,
    VariantKind,
    detect_patronymic,
    split_compound_surname,
)
from gedcom_parser.places import (
    CoordinateKind,
    ParsedPlace,
    PlaceVariant,
    parse_coordinate,
    parse_place_levels,
)
from gedcom_parser.transliteration import is_cyrillic, transliterate_iso9
from gedcom_parser.lexer import iter_lines
from gedcom_parser.models import EncodingInfo, GedcomLine, GedcomRecord
from gedcom_parser.parser import (
    parse_bytes,
    parse_document_file,
    parse_file,
    parse_records,
    parse_text,
)

__version__ = "0.1.0"

__all__ = [
    "BrokenRef",
    "Calendar",
    "CoordinateKind",
    "EncodingInfo",
    "Event",
    "Family",
    "GedcomDateParseError",
    "GedcomDateWarning",
    "GedcomDocument",
    "GedcomEncodingError",
    "GedcomEncodingWarning",
    "GedcomError",
    "GedcomLenientWarning",
    "GedcomLexerError",
    "GedcomLine",
    "GedcomParseError",
    "GedcomRecord",
    "GedcomReferenceWarning",
    "GedcomWarning",
    "Header",
    "MultimediaObject",
    "Name",
    "NameVariant",
    "Note",
    "ParsedDate",
    "ParsedPlace",
    "Person",
    "PlaceVariant",
    "Qualifier",
    "Repository",
    "Source",
    "Submitter",
    "VariantKind",
    "__version__",
    "decode_gedcom",
    "decode_gedcom_file",
    "detect_encoding",
    "detect_patronymic",
    "french_republican_to_gregorian",
    "hebrew_to_gregorian",
    "is_cyrillic",
    "iter_lines",
    "julian_to_gregorian",
    "parse_bytes",
    "parse_coordinate",
    "parse_document_file",
    "parse_file",
    "parse_gedcom_date",
    "parse_place_levels",
    "parse_records",
    "parse_text",
    "split_compound_surname",
    "transliterate_iso9",
]
