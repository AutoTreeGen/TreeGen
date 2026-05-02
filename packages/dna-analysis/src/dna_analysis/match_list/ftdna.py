"""FTDNA Family Finder match-list CSV parser (Phase 16.3).

FTDNA «Family Finder Matches» CSV колонки:

    Full Name, Match Date, Relationship Range, Suggested Relationship,
    Shared Centimorgans, Longest Block, Linked Relationship,
    Ancestral Surnames, Y-DNA Haplogroup, mtDNA Haplogroup,
    Notes, Match Person Name, Email, Kit Number

``Kit Number`` — стабильный external_id у FTDNA.
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

_HEADER_NAME = ("Full Name", "Match Person Name", "Name")
_HEADER_RELATIONSHIP = (
    "Suggested Relationship",
    "Relationship Range",
    "Predicted Relationship",
)
_HEADER_TOTAL_CM = ("Shared Centimorgans", "Total cM", "Shared cM")
_HEADER_LONGEST = ("Longest Block", "Longest Segment", "Largest Segment cM")
_HEADER_SEGMENTS = ("Shared Segments", "Segments", "Match Count")
_HEADER_EXTERNAL_ID = ("Kit Number", "Kit ID", "kit_id")
_HEADER_USERNAME = ("Email",)  # FTDNA выдаёт email как secondary identifier.
_HEADER_NOTES = ("Notes", "Linked Relationship")


def parse_ftdna_match_list(content: str) -> list[MatchListEntry]:
    """Распарсить FTDNA Family-Finder match list CSV."""
    entries: list[MatchListEntry] = []
    for row in read_rows(content):
        external_id = first_present(row, _HEADER_EXTERNAL_ID)
        if not external_id:
            continue
        relationship_raw = first_present(row, _HEADER_RELATIONSHIP)
        entries.append(
            MatchListEntry(
                platform=DnaPlatform.FTDNA,
                external_match_id=external_id,
                display_name=first_present(row, _HEADER_NAME),
                match_username=first_present(row, _HEADER_USERNAME),
                total_cm=parse_optional_float(first_present(row, _HEADER_TOTAL_CM)) or 0.0,
                longest_segment_cm=parse_optional_float(first_present(row, _HEADER_LONGEST)),
                shared_segments_count=parse_optional_int(first_present(row, _HEADER_SEGMENTS)),
                predicted_relationship_raw=relationship_raw,
                predicted_relationship=normalise_relationship(relationship_raw),
                shared_match_count=None,
                notes=first_present(row, _HEADER_NOTES),
                raw_payload=_clean_row(row),
            ),
        )
    return entries


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): (str(v) if v is not None else None) for k, v in row.items()}
