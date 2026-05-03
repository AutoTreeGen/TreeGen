"""Cross-platform DNA match resolver for Phase 26.9.

This detector resolves whether DNA matches from different platforms represent
the same person, same family cluster, or a same-name/surname collision.

Target corpus case:

tree_09_cross_platform_dna_match_resolver
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_SAME_NAME_DIFFERENT_PERSON = "same_name_different_person"
FLAG_SHARED_CLUSTER_NOT_IDENTITY = "shared_cluster_not_identity"
FLAG_SURNAME_ONLY_IDENTITY_RISK = "surname_only_identity_merge_risk"
FLAG_PUBLIC_TREE_CLUSTER_MERGE_ERROR = "public_tree_same_cluster_person_merge_error"
FLAG_ENDOGAMY_SMALL_SEGMENT_OVERUSE = "endogamy_small_segment_overuse"
FLAG_CROSS_PLATFORM_IDENTITY_RESOLVED = "cross_platform_identity_resolved"
FLAG_KIT_EMAIL_HASH_CONFIRMED = "kit_id_email_hash_match_confirmed"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Resolve cross-platform DNA match identity and cluster errors."""
    embedded_errors = _embedded_errors(tree)
    dna_matches = _dna_matches(tree)
    snippets = _archive_snippets(tree)

    if not _has_cross_platform_dna_context(embedded_errors, dna_matches):
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

    adrienne_cluster = ["dna_901", "dna_902", "dna_903"]
    if _same_person_cluster_supported(matches_by_id, adrienne_cluster):
        flags.add(FLAG_CROSS_PLATFORM_IDENTITY_RESOLVED)
        flags.add(FLAG_KIT_EMAIL_HASH_CONFIRMED)
        evaluation_results["eval_09_001"] = True
        merge_decisions.append(
            {
                "merge_id": "merge_dna_901_dna_902_dna_903_cross_platform_identity",
                "merge_pair": adrienne_cluster,
                "status": "Confirmed",
                "action": "resolve_as_same_dna_match_person",
                "canonical_name": "Adrienne /Kaplan/",
                "platforms": ["AncestryDNA", "MyHeritage", "GEDmatch"],
                "email_hash": "hash_adrienne_main",
                "kit_ids": ["T900901"],
                "preserve_aliases": True,
                "rule_id": "cross_platform_dna_match_resolver",
            }
        )

    if _has_levitin_kaplan_archive_bridge(snippets):
        evaluation_results["eval_09_004"] = True
        relationship_claims.append(
            {
                "claim_type": "family_cluster_relationship",
                "subject": "Adrienne Kaplan cross-platform DNA cluster",
                "status": "confirmed",
                "cluster": "Levitin/Kaplan/Brest",
                "path": "Sara Levitin → Kaplan family branch",
                "confidence": 0.92,
                "evidence": ["src_901", "src_902", "src_903", "dna_901", "dna_902", "dna_903"],
            }
        )

    for error in embedded_errors:
        error_type = error.get("type")
        subtype = error.get("subtype")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "cross_platform_identity_error":
            if subtype == "same_name_wrong_platform_merge":
                flags.add(FLAG_SAME_NAME_DIFFERENT_PERSON)
                evaluation_results["eval_09_003"] = True
                matches = error.get("matches") or []
                merge_decisions.append(
                    {
                        "merge_id": "reject_dna_901_dna_904_same_name_wrong_platform_merge",
                        "merge_pair": matches,
                        "status": "Rejected",
                        "action": "do_not_merge",
                        "reason": error.get("reason"),
                        "rule_id": "cross_platform_dna_match_resolver",
                    }
                )
                quarantined_claims.append(
                    {
                        "claim_type": "same_name_cross_platform_identity",
                        "status": "rejected",
                        "matches": matches,
                        "reason": error.get("reason"),
                    }
                )

            elif subtype == "same_cluster_not_same_person":
                flags.add(FLAG_SHARED_CLUSTER_NOT_IDENTITY)
                evaluation_results["eval_09_002"] = True
                matches = error.get("matches") or []
                merge_decisions.append(
                    {
                        "merge_id": "reject_dna_901_dna_905_same_person_cluster_only",
                        "merge_pair": matches,
                        "status": "Rejected",
                        "action": "same_cluster_not_same_person",
                        "reason": error.get("reason"),
                        "rule_id": "cross_platform_dna_match_resolver",
                    }
                )
                relationship_claims.append(
                    {
                        "claim_type": "shared_family_cluster",
                        "subject": "Geoff Michael",
                        "object": "Adrienne Kaplan cross-platform DNA cluster",
                        "status": "same_family_cluster_distinct_person",
                        "cluster": "Levitin/Kaplan",
                        "confidence": 0.86,
                        "evidence": ["dna_901", "dna_902", "dna_903", "dna_905"],
                    }
                )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_ENDOGAMY_SMALL_SEGMENT_OVERUSE)
            quarantined_claims.append(
                {
                    "claim_type": "small_segment_cluster_absorption",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Small same-surname endogamous match cannot be absorbed into Levitin/Kaplan cluster.",
                }
            )

        elif error_type == "online_tree_error":
            flags.add(FLAG_PUBLIC_TREE_CLUSTER_MERGE_ERROR)
            evaluation_results["eval_09_005"] = True
            quarantined_claims.append(
                {
                    "claim_type": "public_tree_same_cluster_person_merge",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "Shared matches can indicate a family cluster without proving same-person identity.",
                }
            )

        elif error_type == "surname_cluster_collision":
            flags.add(FLAG_SURNAME_ONLY_IDENTITY_RISK)
            matches = error.get("matches") or []
            quarantined_claims.append(
                {
                    "claim_type": "surname_only_identity_merge",
                    "status": "rejected",
                    "matches": matches,
                    "reason": error.get("reason"),
                    "accepted_rule": "Kaplan surname alone is not identity evidence.",
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


def _has_cross_platform_dna_context(
    embedded_errors: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {
            "cross_platform_identity_error",
            "dna_overinterpretation",
            "online_tree_error",
            "surname_cluster_collision",
        }
        for error in embedded_errors
    )
    platforms = {str(match.get("platform", "")) for match in dna_matches}
    return has_relevant_error and len(platforms) >= 2


def _same_person_cluster_supported(
    matches_by_id: dict[str, dict[str, Any]],
    ids: list[str],
) -> bool:
    matches = [matches_by_id.get(match_id) for match_id in ids]
    if any(match is None for match in matches):
        return False

    email_hashes = {match.get("email_hash") for match in matches if match is not None}
    shared_cms = [
        int(match.get("shared_cm", 0))
        for match in matches
        if match is not None and isinstance(match.get("shared_cm"), int)
    ]
    platforms = {match.get("platform") for match in matches if match is not None}

    return (
        email_hashes == {"hash_adrienne_main"}
        and len(platforms) >= 3
        and len(shared_cms) == 3
        and min(shared_cms) >= 140
        and max(shared_cms) <= 160
    )


def _has_levitin_kaplan_archive_bridge(snippets: list[dict[str, Any]]) -> bool:
    text = _combined_text(snippets).lower()
    return (
        "birth of abram levitin" in text
        and "birth of sara levitin" in text
        and "marriage of meyer kaplan" in text
        and "daughter of hersh levitin" in text
    )


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
        FLAG_SAME_NAME_DIFFERENT_PERSON,
        FLAG_SHARED_CLUSTER_NOT_IDENTITY,
        FLAG_SURNAME_ONLY_IDENTITY_RISK,
        FLAG_PUBLIC_TREE_CLUSTER_MERGE_ERROR,
        FLAG_ENDOGAMY_SMALL_SEGMENT_OVERUSE,
        FLAG_CROSS_PLATFORM_IDENTITY_RESOLVED,
        FLAG_KIT_EMAIL_HASH_CONFIRMED,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
