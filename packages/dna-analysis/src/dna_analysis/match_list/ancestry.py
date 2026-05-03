"""Ancestry match-list CSV parser (Phase 16.3).

Ancestry экспортирует match-list только через DNAGedcom / clipboard
helpers (Ancestry.com сам без bulk-export для matches на момент
Phase 16.3). Таким образом колонки нестабильны; парсер читает
любую вариацию из known-aliases и преимущественно ищет
``Total cM`` / ``Longest cM`` / ``Predicted Relationship``.

Anti-drift: anti-scraping; принимаем только CSV-загрузку от user'а.
"""

from __future__ import annotations

from typing import Any

from shared_models.enums import DnaPlatform

from dna_analysis.match_list._csv_utils import (
    first_present,
    parse_optional_float,
    parse_optional_int,
    read_rows,
)
from dna_analysis.match_list.models import MatchListEntry
from dna_analysis.match_list.relationship import normalise_relationship

_HEADER_TOTAL_CM = ("Total cM", "TotalCm", "Shared cM", "shared_cm", "Total_CM")
_HEADER_LONGEST = (
    "Longest cM",
    "LongestCm",
    "Longest Segment",
    "longest_segment_cm",
    "LongestSegment",
)
_HEADER_SEGMENTS = ("Segments", "Shared Segments", "shared_segments", "SegmentCount")
_HEADER_NAME = ("Name", "Match Name", "Tester Name", "display_name")
_HEADER_USERNAME = ("Username", "Match Username", "Tester")
_HEADER_RELATIONSHIP = (
    "Predicted Relationship",
    "Relationship",
    "predicted_relationship",
    "Predicted",
)
_HEADER_EXTERNAL_ID = ("Match GUID", "GUID", "Match ID", "TestGuid", "test_guid", "MatchID")
_HEADER_NOTES = ("Notes", "Note", "Tester Note")
_HEADER_SHARED_MATCH_COUNT = ("Shared Matches", "InCommon", "shared_matches_count")


def parse_ancestry_match_list(content: str) -> list[MatchListEntry]:
    """Парсит Ancestry match-list CSV (DNAGedcom / clipboard format).

    Args:
        content: Уже decoded CSV-строка (см. ``_csv_utils.decode_csv_bytes``).

    Returns:
        Список ``MatchListEntry``. Строки без ``external_match_id`` —
        пропускаются (вернуть anchor-less match нельзя, FK-уникальность
        в БД на (kit_id, external_match_id) сорвётся).
    """
    entries: list[MatchListEntry] = []
    for row in read_rows(content):
        external_id = first_present(row, _HEADER_EXTERNAL_ID)
        if not external_id:
            # Без external_id невозможна идемпотентность повторных
            # импортов, и FK-уникальность падает на втором re-import'е.
            continue
        relationship_raw = first_present(row, _HEADER_RELATIONSHIP)
        entries.append(
            MatchListEntry(
                platform=DnaPlatform.ANCESTRY,
                external_match_id=external_id,
                display_name=first_present(row, _HEADER_NAME),
                match_username=first_present(row, _HEADER_USERNAME),
                total_cm=parse_optional_float(first_present(row, _HEADER_TOTAL_CM)) or 0.0,
                longest_segment_cm=parse_optional_float(first_present(row, _HEADER_LONGEST)),
                shared_segments_count=parse_optional_int(first_present(row, _HEADER_SEGMENTS)),
                predicted_relationship_raw=relationship_raw,
                predicted_relationship=normalise_relationship(relationship_raw),
                shared_match_count=parse_optional_int(
                    first_present(row, _HEADER_SHARED_MATCH_COUNT),
                ),
                notes=first_present(row, _HEADER_NOTES),
                raw_payload=_clean_row(row),
            ),
        )
    return entries


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать row к JSON-сериализуемому dict (str→str)."""
    return {str(k): (str(v) if v is not None else None) for k, v in row.items()}
