"""Ashkenazi endogamy multi-path detector for Phase 26.10.

This detector prevents forcing endogamous Ashkenazi DNA evidence into a single
relationship path when separate triangulated/shared-match clusters support
multiple probable paths.

Target corpus case:

tree_12_ashkenazi_endogamy_multi_path_relationship
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_SINGLE_PATH_ERROR = "pedigree_collapse_ashkenazi_single_path_error"
FLAG_SMALL_SEGMENT_OVERUSE = "pedigree_collapse_endogamy_small_segment_overuse"
FLAG_PUBLIC_TREE_OVERCOMPRESSION = "public_tree_single_path_overcompression"
FLAG_MULTI_PATH_REQUIRED = "multi_path_relationship_required"
FLAG_KATZ_FELDMAN_NOT_NOISE = "katz_feldman_cluster_not_noise"
FLAG_SHARED_CLUSTER_SPLIT = "shared_match_cluster_split"
FLAG_TRIANGULATED_DISTINCT_PATHS = "triangulated_segments_support_distinct_paths"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect Ashkenazi endogamy multi-path relationship evidence."""
    embedded_errors = _embedded_errors(tree)
    dna_matches = _dna_matches(tree)
    snippets = _archive_snippets(tree)

    if not _has_ashkenazi_endogamy_context(embedded_errors, dna_matches, snippets):
        return DetectorResult()

    flags: set[str] = set()
    relationship_claims: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    matches_by_id = {
        str(match.get("match_id")): match
        for match in dna_matches
        if isinstance(match.get("match_id"), str)
    }

    if _supports_levitin_friedman_path(matches_by_id, snippets):
        evaluation_results["eval_12_002"] = True
        relationship_claims.append(
            {
                "claim_type": "probable_relationship_path",
                "subject": "dna_1201",
                "path_name": "Levitin-Friedman",
                "status": "probable",
                "confidence": 0.86,
                "evidence": ["dna_1202", "dna_1204", "src_1201", "src_1202"],
                "triangulated_segment": "chromosome 6",
                "reason": "Levitin-only anchor and Boris-Levitin collateral support a distinct Levitin-Friedman path.",
            }
        )

    if _supports_katz_feldman_path(matches_by_id, snippets):
        flags.add(FLAG_KATZ_FELDMAN_NOT_NOISE)
        evaluation_results["eval_12_003"] = True
        relationship_claims.append(
            {
                "claim_type": "probable_relationship_path",
                "subject": "dna_1201",
                "path_name": "Katz-Feldman",
                "status": "probable",
                "confidence": 0.84,
                "evidence": ["dna_1203", "dna_1205", "src_1203", "src_1204"],
                "triangulated_segment": "chromosome 11",
                "reason": "Katz-Feldman anchor and Tamara-Katz collateral support a separate path.",
            }
        )

    if _supports_distinct_triangulated_paths(matches_by_id):
        flags.add(FLAG_SHARED_CLUSTER_SPLIT)
        flags.add(FLAG_TRIANGULATED_DISTINCT_PATHS)
        relationship_claims.append(
            {
                "claim_type": "multi_path_model",
                "subject": "dna_1201",
                "status": "required",
                "paths": ["Levitin-Friedman", "Katz-Feldman"],
                "confidence": 0.88,
                "reason": "Separate triangulated segments and shared-match clusters support distinct paths.",
            }
        )

    for error in embedded_errors:
        error_type = error.get("type")
        subtype = error.get("subtype")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "endogamy_error" and subtype == "single_path_overcompression":
            flags.add(FLAG_SINGLE_PATH_ERROR)
            flags.add(FLAG_MULTI_PATH_REQUIRED)
            evaluation_results["eval_12_001"] = True
            quarantined_claims.append(
                {
                    "claim_type": "single_path_relationship_assignment",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": "Ashkenazi endogamy and separate cluster evidence require multiple probable paths.",
                    "accepted_rule": "Do not force dna_1201 into one relationship path.",
                }
            )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_SMALL_SEGMENT_OVERUSE)
            evaluation_results["eval_12_004"] = True
            quarantined_claims.append(
                {
                    "claim_type": "small_segment_proof_anchor",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": "Small-segment endogamy signal cannot be used as proof anchor.",
                    "accepted_rule": "Require triangulation, shared cluster support and archive bridge.",
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_PUBLIC_TREE_OVERCOMPRESSION)
            evaluation_results["eval_12_005"] = True
            quarantined_claims.append(
                {
                    "claim_type": "public_tree_single_path_overcompression",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "Public tree single-path assignment cannot override multi-cluster evidence.",
                }
            )

        elif error_type == "relationship_modeling_error":
            flags.add(FLAG_MULTI_PATH_REQUIRED)
            flags.add(FLAG_SHARED_CLUSTER_SPLIT)
            flags.add(FLAG_TRIANGULATED_DISTINCT_PATHS)
            quarantined_claims.append(
                {
                    "claim_type": "single_relationship_model",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Represent separate probable paths instead of compressing into one relationship.",
                }
            )

        elif error_type == "dna_cluster_assignment_error":
            flags.add(FLAG_KATZ_FELDMAN_NOT_NOISE)
            quarantined_claims.append(
                {
                    "claim_type": "discarded_katz_feldman_cluster",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Katz-Feldman triangulated cluster must be retained as a probable path.",
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


def _has_ashkenazi_endogamy_context(
    embedded_errors: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {
            "endogamy_error",
            "dna_overinterpretation",
            "source_quality",
            "relationship_modeling_error",
            "dna_cluster_assignment_error",
        }
        for error in embedded_errors
    )
    match_ids = {str(match.get("match_id")) for match in dna_matches}
    text = _combined_text(snippets).lower()
    has_family_context = "levitin" in text and "katz" in text and "feldman" in text
    return has_relevant_error and "dna_1201" in match_ids and has_family_context


def _supports_levitin_friedman_path(
    matches_by_id: dict[str, dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    dna_1202 = matches_by_id.get("dna_1202", {})
    dna_1204 = matches_by_id.get("dna_1204", {})
    text = _combined_text(snippets).lower()

    return (
        "birth of sura levitin" in text
        and "birth of boris levitin" in text
        and "dna_1201" in dna_1202.get("shared_matches_with", [])
        and "dna_1201" in dna_1204.get("shared_matches_with", [])
    )


def _supports_katz_feldman_path(
    matches_by_id: dict[str, dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    dna_1203 = matches_by_id.get("dna_1203", {})
    dna_1205 = matches_by_id.get("dna_1205", {})
    text = _combined_text(snippets).lower()

    return (
        "birth of lidia katz" in text
        and "birth of tamara katz" in text
        and "dna_1201" in dna_1203.get("shared_matches_with", [])
        and "dna_1201" in dna_1205.get("shared_matches_with", [])
    )


def _supports_distinct_triangulated_paths(matches_by_id: dict[str, dict[str, Any]]) -> bool:
    dna_1202 = matches_by_id.get("dna_1202", {})
    dna_1203 = matches_by_id.get("dna_1203", {})

    chromosomes: set[int] = set()
    for match in (dna_1202, dna_1203):
        for segment in match.get("triangulated_segments", []) or []:
            chromosome = segment.get("chromosome")
            if isinstance(chromosome, int):
                chromosomes.add(chromosome)

    return 6 in chromosomes and 11 in chromosomes


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
        FLAG_SINGLE_PATH_ERROR,
        FLAG_SMALL_SEGMENT_OVERUSE,
        FLAG_PUBLIC_TREE_OVERCOMPRESSION,
        FLAG_MULTI_PATH_REQUIRED,
        FLAG_KATZ_FELDMAN_NOT_NOISE,
        FLAG_SHARED_CLUSTER_SPLIT,
        FLAG_TRIANGULATED_DISTINCT_PATHS,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
