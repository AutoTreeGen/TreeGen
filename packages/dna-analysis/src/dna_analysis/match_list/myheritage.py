"""MyHeritage match-list CSV parser (Phase 16.3).

MyHeritage «DNA Matches» CSV expor — колонки:

    Match Name, Match Username, Estimated Relationship,
    Total Shared cM, Largest Segment cM, Number of Shared Segments,
    Match Country, Tree Size, Smart Match, Match ID, Notes

В отличие от Ancestry, у MyHeritage есть стабильный ``Match ID`` —
прямой external_id.
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

_HEADER_NAME = ("Match Name", "Name", "Display Name")
_HEADER_USERNAME = ("Match Username", "Username")
_HEADER_RELATIONSHIP = ("Estimated Relationship", "Relationship", "Predicted Relationship")
_HEADER_TOTAL_CM = ("Total Shared cM", "Total cM", "Shared cM")
_HEADER_LONGEST = ("Largest Segment cM", "Largest Segment", "Longest Segment cM")
_HEADER_SEGMENTS = ("Number of Shared Segments", "Segments", "Shared Segments")
_HEADER_EXTERNAL_ID = ("Match ID", "MatchId", "match_id")
_HEADER_TREE_SIZE = ("Tree Size", "tree_size")
_HEADER_NOTES = ("Notes", "Note")


def parse_myheritage_match_list(content: str) -> list[MatchListEntry]:
    """Распарсить MyHeritage DNA-matches CSV.

    Tree-size попадает в ``notes`` (если присутствует) — это полезный
    сигнал «у этого мэтча большое/маленькое дерево» для планирования
    research, и legacy notes-колонка поглотит его без расширения схемы.
    """
    entries: list[MatchListEntry] = []
    for row in read_rows(content):
        external_id = first_present(row, _HEADER_EXTERNAL_ID)
        if not external_id:
            continue
        relationship_raw = first_present(row, _HEADER_RELATIONSHIP)
        notes = first_present(row, _HEADER_NOTES)
        tree_size = first_present(row, _HEADER_TREE_SIZE)
        if tree_size and tree_size.strip():
            tree_note = f"tree_size={tree_size.strip()}"
            notes = f"{notes}; {tree_note}" if notes else tree_note
        entries.append(
            MatchListEntry(
                platform=DnaPlatform.MYHERITAGE,
                external_match_id=external_id,
                display_name=first_present(row, _HEADER_NAME),
                match_username=first_present(row, _HEADER_USERNAME),
                total_cm=parse_optional_float(first_present(row, _HEADER_TOTAL_CM)) or 0.0,
                longest_segment_cm=parse_optional_float(first_present(row, _HEADER_LONGEST)),
                shared_segments_count=parse_optional_int(first_present(row, _HEADER_SEGMENTS)),
                predicted_relationship_raw=relationship_raw,
                predicted_relationship=normalise_relationship(relationship_raw),
                shared_match_count=None,
                notes=notes,
                raw_payload=_clean_row(row),
            ),
        )
    return entries


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): (str(v) if v is not None else None) for k, v in row.items()}
