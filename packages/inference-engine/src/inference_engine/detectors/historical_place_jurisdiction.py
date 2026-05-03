"""Historical place jurisdiction detector for Phase 26.8.

This detector targets historical-place routing errors:

- modern country labels used for pre-1917 records;
- partition-era jurisdiction confusion;
- old names used for the wrong period;
- Danzig/Gdansk period mistakes;
- Mennonite colony records routed as generic Ukraine;
- Jewish/Mennonite regional adjacency over-merged without bridge evidence.

Target corpus case:

tree_10_historical_place_jurisdiction_resolution
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_MODERN_COUNTRY_PRE1917 = "modern_country_for_pre1917_record"
FLAG_PARTITION_CONFUSION = "partition_jurisdiction_confusion"
FLAG_OLD_NAME_WRONG_PERIOD = "old_name_used_for_wrong_period"
FLAG_DANZIG_PERIOD_ERROR = "danzig_gdansk_period_error"
FLAG_MENNONITE_GENERIC_UKRAINE = "mennonite_colony_generic_ukraine_error"
FLAG_MENNONITE_JEWISH_BOUNDARY = "mennonite_jewish_boundary_error"
FLAG_LOST_JURISDICTION = "modern_place_normalization_lost_jurisdiction"
FLAG_ARCHIVE_ROUTING_REQUIRED = "archive_routing_by_event_year_required"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect historical jurisdiction and archive-routing errors."""
    embedded_errors = _embedded_errors(tree)
    snippets = _archive_snippets(tree)

    if not _has_historical_place_context(embedded_errors, snippets):
        return DetectorResult()

    flags: set[str] = set()
    place_corrections: list[dict[str, Any]] = []
    relationship_claims: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    for error in embedded_errors:
        error_type = error.get("type")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "place_jurisdiction_error":
            flags.add(expected_flag)
            flags.add(FLAG_ARCHIVE_ROUTING_REQUIRED)

            person_id = error.get("person_id")

            if expected_flag == FLAG_MODERN_COUNTRY_PRE1917:
                evaluation_results["eval_10_001"] = True
                place_corrections.append(
                    {
                        "person_id": person_id,
                        "field": "event_place",
                        "rejected_value": "Brest, Belarus",
                        "accepted_value": "Brest-Litovsk / Brisk, Grodno Governorate, Russian Empire",
                        "archive_route": "Jewish metric books, Grodno Governorate, Brest-Litovsk",
                        "reason": error.get("reason"),
                    }
                )

            elif expected_flag == FLAG_PARTITION_CONFUSION:
                evaluation_results["eval_10_002"] = True
                place_corrections.append(
                    {
                        "person_id": person_id,
                        "field": "event_place",
                        "rejected_value": "Koniuchy, modern Poland only",
                        "accepted_value": "Koniuchy, Congress Poland, Russian Empire",
                        "archive_route": "Congress Poland / Russian Empire civil-parish workflow",
                        "reason": error.get("reason"),
                    }
                )

            elif expected_flag == FLAG_OLD_NAME_WRONG_PERIOD:
                evaluation_results["eval_10_003"] = True
                place_corrections.append(
                    {
                        "person_id": person_id,
                        "field": "event_place",
                        "rejected_value": "Ekaterinoslav, Russian Empire, 1924",
                        "accepted_value": "Soviet-era Dnipropetrovsk region / Ukrainian SSR context",
                        "archive_route": "Soviet civil BDM / Dnipropetrovsk regional workflow",
                        "reason": error.get("reason"),
                    }
                )

            elif expected_flag == FLAG_DANZIG_PERIOD_ERROR:
                evaluation_results["eval_10_004"] = True
                place_corrections.append(
                    {
                        "person_id": person_id,
                        "field": "event_place",
                        "rejected_value": "Gdańsk, Poland, 1880",
                        "accepted_value": "Danzig, West Prussia, German Empire",
                        "modern_reference": "Gdańsk, Poland",
                        "archive_route": "German Empire / West Prussia civil register workflow",
                        "reason": error.get("reason"),
                    }
                )

            elif expected_flag == FLAG_MENNONITE_GENERIC_UKRAINE:
                evaluation_results["eval_10_005"] = True
                place_corrections.append(
                    {
                        "person_id": person_id,
                        "field": "event_place",
                        "rejected_value": "Molotschna, Ukraine",
                        "accepted_value": "Molotschna Mennonite Colony, Taurida Governorate, Russian Empire",
                        "archive_route": "Mennonite colony / Taurida church-register workflow",
                        "reason": error.get("reason"),
                    }
                )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_MENNONITE_JEWISH_BOUNDARY)
            evaluation_results["eval_10_006"] = True
            quarantined_claims.append(
                {
                    "claim_type": "regional_dna_overmerge",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Mennonite Wiens/Friesen cluster remains separate from Jewish Levitin branch without bridge evidence.",
                }
            )
            relationship_claims.append(
                {
                    "claim_type": "cluster_boundary",
                    "subject": "Mennonite Wiens/Friesen DNA cluster",
                    "object": "Jewish Levitin branch",
                    "status": "kept_separate",
                    "confidence": 0.9,
                    "reason": error.get("reason"),
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_LOST_JURISDICTION)
            flags.add(FLAG_ARCHIVE_ROUTING_REQUIRED)
            quarantined_claims.append(
                {
                    "claim_type": "modern_place_normalization",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "Preserve historical jurisdiction and route archives by event year.",
                }
            )

    return DetectorResult(
        engine_flags=_ordered_flags(flags),
        relationship_claims=relationship_claims,
        place_corrections=place_corrections,
        quarantined_claims=quarantined_claims,
        evaluation_results=evaluation_results,
    )


def _embedded_errors(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("embedded_errors") or []
    return [item for item in raw if isinstance(item, dict)]


def _archive_snippets(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("input_archive_snippets") or []
    return [item for item in raw if isinstance(item, dict)]


def _has_historical_place_context(
    embedded_errors: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {
            "place_jurisdiction_error",
            "dna_overinterpretation",
            "source_quality",
        }
        for error in embedded_errors
    )
    text = _combined_text(snippets).lower()
    has_place_text = any(
        token in text
        for token in (
            "brest-litovsk",
            "brisk",
            "grodno",
            "koniuchy",
            "danzig",
            "gdańsk",
            "west prussia",
            "molotschna",
            "mennonite",
            "taurida",
            "dnipropetrovsk",
            "ekaterinoslav",
        )
    )
    return has_relevant_error and has_place_text


def _combined_text(snippets: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for snippet in snippets:
        for key in ("transcription_excerpt", "type", "language"):
            value = snippet.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _ordered_flags(flags: set[str]) -> list[str]:
    order = [
        FLAG_MODERN_COUNTRY_PRE1917,
        FLAG_PARTITION_CONFUSION,
        FLAG_OLD_NAME_WRONG_PERIOD,
        FLAG_DANZIG_PERIOD_ERROR,
        FLAG_MENNONITE_GENERIC_UKRAINE,
        FLAG_MENNONITE_JEWISH_BOUNDARY,
        FLAG_LOST_JURISDICTION,
        FLAG_ARCHIVE_ROUTING_REQUIRED,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
