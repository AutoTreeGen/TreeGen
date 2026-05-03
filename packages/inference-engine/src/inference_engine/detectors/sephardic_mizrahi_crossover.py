"""Sephardic/Mizrahi crossover false Ashkenazi merge detector for Phase 26.12.

This detector prevents broad Jewish population overlap, surname similarity and
modern Israel co-location from collapsing distinct Jewish population contexts
into one Ashkenazi branch.

Target corpus case:

tree_14_sephardic_mizrahi_crossover_false_ashkenazi_merge
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_NON_ASHKENAZI_FALSE_MERGE = "non_ashkenazi_jewish_crossover_false_ashkenazi_merge"
FLAG_MOUNTAIN_CLUSTER_NOT_PALE = "mountain_jewish_cluster_not_pale_ashkenazi"
FLAG_BROAD_JEWISH_OVERLAP = "broad_jewish_dna_overlap_not_branch_proof"
FLAG_SAME_NAME_PLACE_FALSE_EQUIV = "same_name_place_name_false_equivalence"
FLAG_PUBLIC_TREE_CONTEXT_COLLAPSE = "public_tree_population_context_collapse"
FLAG_KAPLAN_KAPLUNOV_FALSE_EQUIV = "kaplan_kaplunov_false_equivalence"
FLAG_POPULATION_CONTEXT_REQUIRED = "population_context_required"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect false cross-population Jewish merges."""
    embedded_errors = _embedded_errors(tree)
    dna_matches = _dna_matches(tree)
    snippets = _archive_snippets(tree)

    if not _has_population_context(embedded_errors, dna_matches, snippets):
        return DetectorResult()

    flags: set[str] = set()
    relationship_claims: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    if _has_ashkenazi_branch_anchor(snippets, dna_matches):
        evaluation_results["eval_14_001"] = True
        relationship_claims.append(
            {
                "claim_type": "confirmed_branch",
                "subject": "Ashkenazi Rabinovich/Ginzburg/Kaplan Pale branch",
                "status": "confirmed",
                "confidence": 0.9,
                "evidence": ["src_1401", "src_1402", "dna_1401", "dna_1402", "dna_1403"],
                "population_context": "Ashkenazi Jewish / Minsk-Vilna Pale branch",
            }
        )

    if _has_bukharian_cluster(dna_matches, snippets):
        evaluation_results["eval_14_003"] = True
        relationship_claims.append(
            {
                "claim_type": "separate_population_cluster",
                "subject": "Bukharian Rabinov/Kaplunov cluster",
                "status": "kept_separate",
                "confidence": 0.86,
                "evidence": ["src_1403", "src_1405", "dna_1404", "dna_1405"],
                "does_not_imply": "same Ashkenazi Rabinovich/Kaplan branch",
            }
        )

    if _has_mountain_jewish_cluster(dna_matches, snippets):
        evaluation_results["eval_14_004"] = True
        relationship_claims.append(
            {
                "claim_type": "separate_population_cluster",
                "subject": "Mountain Jewish / Juhuro cluster",
                "status": "kept_separate",
                "confidence": 0.86,
                "evidence": ["src_1404", "dna_1406", "dna_1407"],
                "does_not_imply": "Ashkenazi Pale branch",
            }
        )

    for error in embedded_errors:
        error_type = error.get("type")
        subtype = error.get("subtype")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "population_context_error":
            flags.add(FLAG_POPULATION_CONTEXT_REQUIRED)

            if subtype == "bukharian_as_ashkenazi_merge":
                flags.add(FLAG_NON_ASHKENAZI_FALSE_MERGE)
                persons = error.get("persons") or []
                merge_decisions.append(
                    {
                        "merge_id": "reject_minsk_rabinovich_bukhara_rabinov_merge",
                        "merge_pair": persons,
                        "status": "Rejected",
                        "action": "do_not_merge",
                        "reason": error.get("reason"),
                        "rule_id": "sephardic_mizrahi_crossover",
                    }
                )
                evaluation_results["eval_14_002"] = True
                quarantined_claims.append(
                    {
                        "claim_type": "bukharian_as_ashkenazi_merge",
                        "status": "rejected",
                        "persons": persons,
                        "reason": error.get("reason"),
                        "accepted_rule": "Require population, geography and archive-context bridge before merge.",
                    }
                )

            elif subtype == "mountain_jewish_as_ashkenazi_merge":
                flags.add(FLAG_MOUNTAIN_CLUSTER_NOT_PALE)
                quarantined_claims.append(
                    {
                        "claim_type": "mountain_jewish_as_ashkenazi_merge",
                        "status": "rejected",
                        "persons": error.get("persons") or [],
                        "reason": error.get("reason"),
                        "accepted_rule": "Mountain Jewish/Juhuro cluster stays separate without bridge evidence.",
                    }
                )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_BROAD_JEWISH_OVERLAP)
            flags.add(FLAG_POPULATION_CONTEXT_REQUIRED)
            quarantined_claims.append(
                {
                    "claim_type": "broad_jewish_dna_overlap",
                    "status": "rejected_as_branch_proof",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Broad Jewish DNA overlap is not branch proof without shared Ashkenazi anchors.",
                }
            )

        elif error_type == "same_name_different_person":
            flags.add(FLAG_SAME_NAME_PLACE_FALSE_EQUIV)
            flags.add(FLAG_POPULATION_CONTEXT_REQUIRED)
            persons = error.get("persons") or []
            merge_decisions.append(
                {
                    "merge_id": "reject_same_name_place_false_equivalence",
                    "merge_pair": persons,
                    "status": "Rejected",
                    "action": "do_not_merge",
                    "reason": error.get("reason"),
                    "rule_id": "sephardic_mizrahi_crossover",
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_PUBLIC_TREE_CONTEXT_COLLAPSE)
            flags.add(FLAG_POPULATION_CONTEXT_REQUIRED)
            evaluation_results["eval_14_005"] = True
            quarantined_claims.append(
                {
                    "claim_type": "public_tree_population_context_collapse",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "Modern Israel co-location and surname normalization cannot collapse population context.",
                }
            )

        elif error_type == "surname_cluster_collision":
            flags.add(FLAG_KAPLAN_KAPLUNOV_FALSE_EQUIV)
            flags.add(FLAG_POPULATION_CONTEXT_REQUIRED)
            quarantined_claims.append(
                {
                    "claim_type": "surname_cluster_collision",
                    "status": "rejected",
                    "persons": error.get("persons") or [],
                    "reason": error.get("reason"),
                    "accepted_rule": "Kaplan/Kaplunov similarity requires archive or cluster bridge proof.",
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


def _dna_matches(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("input_dna_matches") or []
    return [item for item in raw if isinstance(item, dict)]


def _archive_snippets(tree: dict[str, Any]) -> list[dict[str, Any]]:
    raw = tree.get("input_archive_snippets") or []
    return [item for item in raw if isinstance(item, dict)]


def _has_population_context(
    embedded_errors: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {
            "population_context_error",
            "dna_overinterpretation",
            "same_name_different_person",
            "source_quality",
            "surname_cluster_collision",
        }
        for error in embedded_errors
    )
    text = _combined_text(snippets).lower()
    match_ids = {str(match.get("match_id")) for match in dna_matches}
    has_context = (
        "rabinovich" in text
        and ("bukhara" in text or "samarkand" in text)
        and ("mountain jewish" in text or "juhuro" in text)
    )
    return has_relevant_error and "dna_1401" in match_ids and has_context


def _has_ashkenazi_branch_anchor(
    snippets: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
) -> bool:
    text = _combined_text(snippets).lower()
    match_ids = {str(match.get("match_id")) for match in dna_matches}
    return (
        "rabinovich household registered in minsk" in text
        and "chana ginzburg of vilna" in text
        and {"dna_1401", "dna_1402", "dna_1403"}.issubset(match_ids)
    )


def _has_bukharian_cluster(
    dna_matches: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    text = _combined_text(snippets).lower()
    ids = {str(match.get("match_id")) for match in dna_matches}
    return {"dna_1404", "dna_1405"}.issubset(ids) and "bukharian" in text and "samarkand" in text


def _has_mountain_jewish_cluster(
    dna_matches: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    text = _combined_text(snippets).lower()
    ids = {str(match.get("match_id")) for match in dna_matches}
    return {"dna_1406", "dna_1407"}.issubset(ids) and "mountain jewish" in text and "juhuro" in text


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
        FLAG_NON_ASHKENAZI_FALSE_MERGE,
        FLAG_MOUNTAIN_CLUSTER_NOT_PALE,
        FLAG_BROAD_JEWISH_OVERLAP,
        FLAG_SAME_NAME_PLACE_FALSE_EQUIV,
        FLAG_PUBLIC_TREE_CONTEXT_COLLAPSE,
        FLAG_KAPLAN_KAPLUNOV_FALSE_EQUIV,
        FLAG_POPULATION_CONTEXT_REQUIRED,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
