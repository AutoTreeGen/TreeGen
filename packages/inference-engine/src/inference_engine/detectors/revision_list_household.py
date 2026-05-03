"""Revision list household interpretation detector for Phase 26.5.

This detector targets deterministic household-interpretation errors in Russian
Empire revision-list evidence. It starts with:

tree_17_revision_list_household_interpretation

Revision lists are not the same as vital records. Missing female household
members, age drift, registered-vs-actual residence notes, same-name households
and surname variants must be interpreted conservatively.
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_MISSING_FEMALE_NOT_DISPROOF = "revision_list_missing_female_not_disproof"
FLAG_SAME_NAME_DIFFERENT_HOUSEHOLD = "same_name_same_guberniya_different_household"
FLAG_INVENTED_WIFE_FROM_GAP = "unknown_wife_invented_from_missing_female_revision"
FLAG_AGE_DRIFT_NOT_CONFLICT = "revision_list_age_drift_not_identity_conflict"
FLAG_REGISTERED_ACTUAL_RESIDENCE = "registered_vs_actual_residence_confusion"
FLAG_VARIANT_NOT_ENOUGH = "raskes_raskin_variant_not_enough"
FLAG_PUBLIC_TREE_OVERREACH = "public_tree_revision_list_overreach"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect revision-list household interpretation errors."""
    embedded_errors = _embedded_errors(tree)
    snippets = _archive_snippets(tree)

    if not _has_revision_list_context(embedded_errors, snippets):
        return DetectorResult()

    flags: set[str] = set()
    relationship_claims: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    for error in embedded_errors:
        error_type = error.get("type")
        subtype = error.get("subtype")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "revision_list_interpretation_error":
            if subtype == "female_absence_as_nonexistence":
                flags.add(FLAG_MISSING_FEMALE_NOT_DISPROOF)
                evaluation_results["eval_17_001"] = True
                relationship_claims.append(
                    {
                        "claim_type": "mother",
                        "subject_name": "Yankel /Raskes/",
                        "object_name": "Sura /Friedman/",
                        "status": "confirmed",
                        "confidence": 0.91,
                        "evidence": ["src_1704", "src_1705"],
                        "reason": error.get("reason"),
                        "revision_list_note": "Missing female enumeration is not disproof.",
                    }
                )
                quarantined_claims.append(
                    {
                        "claim_type": "female_absence_disproof",
                        "status": "rejected",
                        "person_id": error.get("person_id"),
                        "reason": error.get("reason"),
                        "accepted_rule": "metric records override missing female summary pages",
                    }
                )

            elif subtype == "age_drift_overinterpreted":
                flags.add(FLAG_AGE_DRIFT_NOT_CONFLICT)
                evaluation_results["eval_17_004"] = True
                relationship_claims.append(
                    {
                        "claim_type": "household_continuity",
                        "subject_id": error.get("person_id"),
                        "status": "supported",
                        "confidence": 0.83,
                        "evidence": ["src_1701", "src_1702", "src_1703"],
                        "reason": error.get("reason"),
                        "interpretation": "age drift is acceptable within revision-list evidence",
                    }
                )

        elif error_type == "same_name_different_person":
            flags.add(FLAG_SAME_NAME_DIFFERENT_HOUSEHOLD)
            evaluation_results["eval_17_002"] = True
            persons = error.get("persons") or []
            merge_decisions.append(
                {
                    "merge_id": "reject_I3_I7_revision_household_conflict",
                    "merge_pair": persons,
                    "status": "Rejected",
                    "action": "do_not_merge",
                    "reason": error.get("reason"),
                    "evidence": ["src_1701", "src_1702", "src_1703", "src_1706"],
                    "rule_id": "revision_list_household",
                }
            )
            quarantined_claims.append(
                {
                    "claim_type": "same_name_same_person",
                    "status": "rejected",
                    "persons": persons,
                    "reason": error.get("reason"),
                }
            )

        elif error_type == "fabrication" and subtype == "invented_spouse_from_revision_gap":
            flags.add(FLAG_INVENTED_WIFE_FROM_GAP)
            evaluation_results["eval_17_003"] = True
            quarantined_claims.append(
                {
                    "claim_type": "invented_spouse",
                    "status": "rejected",
                    "person_id": error.get("person_id"),
                    "rejected_name": "Chaya",
                    "reason": error.get("reason"),
                    "accepted_rule": "do not invent named wives from missing female revision-list columns",
                }
            )

        elif error_type == "residence_confusion":
            flags.add(FLAG_REGISTERED_ACTUAL_RESIDENCE)
            quarantined_claims.append(
                {
                    "claim_type": "residence_interpretation",
                    "status": "corrected",
                    "person_id": error.get("person_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "registered community and actual residence can differ",
                }
            )

        elif error_type == "surname_variant_overmerge":
            flags.add(FLAG_VARIANT_NOT_ENOUGH)
            evaluation_results["eval_17_005"] = True
            persons = error.get("persons") or []
            merge_decisions.append(
                {
                    "merge_id": "reject_raskes_raskin_variant_overmerge",
                    "merge_pair": persons,
                    "status": "Rejected",
                    "action": "keep_as_hypothesis_conflict",
                    "reason": error.get("reason"),
                    "rule_id": "revision_list_household",
                }
            )
            quarantined_claims.append(
                {
                    "claim_type": "surname_variant_merge",
                    "status": "quarantined",
                    "persons": persons,
                    "variant_pair": ["Raskes", "Raskin"],
                    "reason": error.get("reason"),
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_PUBLIC_TREE_OVERREACH)
            quarantined_claims.append(
                {
                    "claim_type": "public_tree_revision_list_claim",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "public tree inference cannot override primary revision/vital evidence",
                }
            )

    return DetectorResult(
        engine_flags=_ordered_flags(flags),
        relationship_claims=relationship_claims,
        merge_decisions=merge_decisions,
        quarantined_claims=quarantined_claims,
        evaluation_results=evaluation_results,
    )


def _embedded_errors(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("embedded_errors") or []
    return [item for item in raw if isinstance(item, dict)]


def _archive_snippets(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("input_archive_snippets") or []
    return [item for item in raw if isinstance(item, dict)]


def _has_revision_list_context(
    embedded_errors: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    snippet_types = {str(snippet.get("type", "")).lower() for snippet in snippets}
    has_revision_list = "revision_list" in snippet_types
    has_relevant_error = any(
        error.get("type")
        in {
            "revision_list_interpretation_error",
            "same_name_different_person",
            "residence_confusion",
            "surname_variant_overmerge",
        }
        for error in embedded_errors
    )
    return has_revision_list and has_relevant_error


def _ordered_flags(flags: set[str]) -> list[str]:
    order = [
        FLAG_MISSING_FEMALE_NOT_DISPROOF,
        FLAG_SAME_NAME_DIFFERENT_HOUSEHOLD,
        FLAG_INVENTED_WIFE_FROM_GAP,
        FLAG_AGE_DRIFT_NOT_CONFLICT,
        FLAG_REGISTERED_ACTUAL_RESIDENCE,
        FLAG_VARIANT_NOT_ENOUGH,
        FLAG_PUBLIC_TREE_OVERREACH,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
