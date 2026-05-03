"""23andMe match-list CSV parser (Phase 16.3).

23andMe «Relatives in DNA Relatives» экспорт. Колонки (per их CSV в
Phase 16.3 на момент написания):

    Display Name, Sex, Predicted Relationship, Relationship Range,
    % DNA Shared, # Segments, Largest Segment (cM), Total cM,
    Maternal/Paternal/Both, Has Y, Has mtDNA, Y Haplogroup,
    mtDNA Haplogroup, Profile

Profile-URL содержит «/profile/<external_id>», откуда и парсим
external_match_id.
"""

from __future__ import annotations

import re
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

_PROFILE_ID = re.compile(r"/profile/([A-Za-z0-9_-]+)")

_HEADER_NAME = ("Display Name", "Name")
_HEADER_RELATIONSHIP = ("Predicted Relationship", "Relationship")
_HEADER_TOTAL_CM = ("Total cM", "TotalCm")
_HEADER_LONGEST = ("Largest Segment (cM)", "Largest Segment", "Longest Segment cM")
_HEADER_SEGMENTS = ("# Segments", "Segments")
_HEADER_PROFILE = ("Profile", "Profile URL", "Profile Link")
_HEADER_USERNAME = ("Username", "Profile Username")


def parse_twentythree_and_me_match_list(content: str) -> list[MatchListEntry]:
    """Распарсить 23andMe Relatives-in-DNA-Relatives CSV.

    23andMe не выдаёт прямо external_id; берём его из ссылки в колонке
    ``Profile``. Если в строке нет profile-URL и нет username — строка
    пропускается (без external_id невозможна идемпотентность).
    """
    entries: list[MatchListEntry] = []
    for row in read_rows(content):
        profile = first_present(row, _HEADER_PROFILE)
        username = first_present(row, _HEADER_USERNAME)
        external_id = _extract_external_id(profile, username)
        if not external_id:
            continue
        relationship_raw = first_present(row, _HEADER_RELATIONSHIP)
        entries.append(
            MatchListEntry(
                platform=DnaPlatform.TWENTY_THREE,
                external_match_id=external_id,
                display_name=first_present(row, _HEADER_NAME),
                match_username=username,
                total_cm=parse_optional_float(first_present(row, _HEADER_TOTAL_CM)) or 0.0,
                longest_segment_cm=parse_optional_float(first_present(row, _HEADER_LONGEST)),
                shared_segments_count=parse_optional_int(first_present(row, _HEADER_SEGMENTS)),
                predicted_relationship_raw=relationship_raw,
                predicted_relationship=normalise_relationship(relationship_raw),
                shared_match_count=None,  # 23andMe не выдаёт shared-match-count в этом экспорте.
                notes=None,
                raw_payload=_clean_row(row),
            ),
        )
    return entries


def _extract_external_id(profile: str | None, username: str | None) -> str | None:
    """Достать external_id из profile-URL; иначе fallback на username."""
    if profile:
        match = _PROFILE_ID.search(profile)
        if match:
            return match.group(1)
    if username and username.strip():
        return username.strip()
    return None


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): (str(v) if v is not None else None) for k, v in row.items()}
