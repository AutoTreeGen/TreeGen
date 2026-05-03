"""GEDmatch one-to-many match-list CSV parser (Phase 16.3).

GEDmatch «One-to-many» tool позволяет скачать результаты сравнения
своего kit'а со всеми остальными в БД. CSV колонки (Tier-1 export):

    Kit Num, Name, Email, Total cM, Largest cM, Gen, Overlap,
    Date Compared

``Kit Num`` — это stable external_id (e.g. «A123456»). «Gen» —
оценочный generation distance (1.0 = full sibling, 4.5 = 4th-5th
cousin). Эту шкалу мы преобразуем в наш ``PredictedRelationship`` enum
в дополнение к маппингу по тексту.
"""

from __future__ import annotations

from typing import Any

from shared_models.enums import DnaPlatform, PredictedRelationship

from dna_analysis.match_list._csv_utils import (
    first_present,
    parse_optional_float,
    parse_optional_int,
    read_rows,
)
from dna_analysis.match_list.models import MatchListEntry
from dna_analysis.match_list.relationship import normalise_relationship

_HEADER_NAME = ("Name", "Match Name")
_HEADER_USERNAME = ("Email",)
_HEADER_TOTAL_CM = ("Total cM", "Total_cM", "Shared cM")
_HEADER_LONGEST = ("Largest cM", "Largest_cM", "Longest Segment cM")
_HEADER_OVERLAP = ("Overlap", "SNP Overlap", "Overlap SNPs")
_HEADER_EXTERNAL_ID = ("Kit Num", "Kit ID", "Kit Number")
_HEADER_GEN = ("Gen", "Generation", "Estimated Generation")
_HEADER_RELATIONSHIP = ("Estimated Relationship", "Relationship", "Predicted Relationship")


def parse_gedmatch_match_list(content: str) -> list[MatchListEntry]:
    """Распарсить GEDmatch one-to-many CSV.

    Если есть ``Estimated Relationship`` колонка — используем
    text-mapping. Если нет, маппим из ``Gen`` (оценочное число
    поколений до общего предка) в наш bucket.
    """
    entries: list[MatchListEntry] = []
    for row in read_rows(content):
        external_id = first_present(row, _HEADER_EXTERNAL_ID)
        if not external_id:
            continue
        relationship_raw = first_present(row, _HEADER_RELATIONSHIP)
        if relationship_raw:
            relationship = normalise_relationship(relationship_raw)
        else:
            gen_value = parse_optional_float(first_present(row, _HEADER_GEN))
            relationship = _gen_to_relationship(gen_value)
            relationship_raw = f"gen={gen_value:.1f}" if gen_value is not None else None
        entries.append(
            MatchListEntry(
                platform=DnaPlatform.GEDMATCH,
                external_match_id=external_id,
                display_name=first_present(row, _HEADER_NAME),
                match_username=first_present(row, _HEADER_USERNAME),
                total_cm=parse_optional_float(first_present(row, _HEADER_TOTAL_CM)) or 0.0,
                longest_segment_cm=parse_optional_float(first_present(row, _HEADER_LONGEST)),
                shared_segments_count=parse_optional_int(first_present(row, _HEADER_OVERLAP)),
                predicted_relationship_raw=relationship_raw,
                predicted_relationship=relationship,
                shared_match_count=None,
                notes=None,
                raw_payload=_clean_row(row),
            ),
        )
    return entries


def _gen_to_relationship(gen: float | None) -> PredictedRelationship:
    """Перевести GEDmatch ``Gen`` (generation distance) в bucket.

    Маппинг по ISOGG cM/Gen tables: 1.0=full_sibling/parent_child,
    2.0=uncle/aunt/half-sib, 2.5=1st cousin, 3.5=2nd cousin,
    4.5=3rd cousin, 5.5+=4th-6th cousin, 7+=distant.
    """
    if gen is None:
        return PredictedRelationship.UNKNOWN
    if gen < 1.5:
        # GEDmatch не отличает parent-child от full sibling в этой шкале.
        # Trust platform: defaultим в FULL_SIBLING (статистически чаще).
        return PredictedRelationship.FULL_SIBLING
    if gen < 2.5:
        return PredictedRelationship.HALF_SIBLING_OR_UNCLE_AUNT
    if gen < 3.0:
        return PredictedRelationship.FIRST_COUSIN
    if gen < 4.0:
        return PredictedRelationship.SECOND_COUSIN
    if gen < 5.0:
        return PredictedRelationship.THIRD_COUSIN
    if gen < 7.0:
        return PredictedRelationship.FOURTH_TO_SIXTH_COUSIN
    return PredictedRelationship.DISTANT


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k): (str(v) if v is not None else None) for k, v in row.items()}
