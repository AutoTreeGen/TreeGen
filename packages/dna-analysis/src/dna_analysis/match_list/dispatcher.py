"""Match-list dispatcher: маршрутизация платформа → парсер (Phase 16.3).

Один публичный entrypoint для всех платформ:

    parse_match_list(content_bytes, platform=DnaPlatform.ANCESTRY)
        → list[MatchListEntry]

Парсер выбирается по platform-енуму; encoding-decode из bytes — здесь
(одна точка для UTF-8/Windows-1252 fallback'а).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from shared_models.enums import DnaPlatform

from dna_analysis.match_list._csv_utils import decode_csv_bytes
from dna_analysis.match_list.ancestry import parse_ancestry_match_list
from dna_analysis.match_list.ftdna import parse_ftdna_match_list
from dna_analysis.match_list.gedmatch import parse_gedmatch_match_list
from dna_analysis.match_list.models import MatchListEntry
from dna_analysis.match_list.myheritage import parse_myheritage_match_list
from dna_analysis.match_list.twentythree_and_me import parse_twentythree_and_me_match_list

_DISPATCH: dict[DnaPlatform, Callable[[str], list[MatchListEntry]]] = {
    DnaPlatform.ANCESTRY: parse_ancestry_match_list,
    DnaPlatform.TWENTY_THREE: parse_twentythree_and_me_match_list,
    DnaPlatform.MYHERITAGE: parse_myheritage_match_list,
    DnaPlatform.FTDNA: parse_ftdna_match_list,
    DnaPlatform.GEDMATCH: parse_gedmatch_match_list,
}


class UnsupportedPlatformError(ValueError):
    """Платформа не поддерживается match-list ingest'ом (Phase 16.3)."""


def parse_match_list(
    source: bytes | str | Path,
    platform: DnaPlatform,
) -> list[MatchListEntry]:
    """Распарсить match-list CSV для указанной платформы.

    Args:
        source: bytes (raw upload), str (already-decoded text), или Path
            к локальному файлу (для CLI / тестов).
        platform: какой платформенный CSV формат ожидается.

    Returns:
        Список ``MatchListEntry``. Строки без ``external_match_id``
        пропускаются на уровне per-platform парсера; этот dispatcher
        не делает свой filter — каждый платформенный парсер сам
        принимает решение.

    Raises:
        UnsupportedPlatformError: ``platform`` не в ``_DISPATCH``.
        UnicodeDecodeError: bytes не декодируются ни в UTF-8 ни в
            Windows-1252.
    """
    parser = _DISPATCH.get(platform)
    if parser is None:
        msg = f"match-list ingest does not support platform={platform.value}"
        raise UnsupportedPlatformError(msg)

    if isinstance(source, Path):
        content = decode_csv_bytes(source.read_bytes())
    elif isinstance(source, bytes):
        content = decode_csv_bytes(source)
    else:
        content = source
    return parser(content)


def supported_platforms() -> tuple[DnaPlatform, ...]:
    """Список платформ, поддерживаемых match-list ingest (для UI/healthz)."""
    return tuple(_DISPATCH.keys())
