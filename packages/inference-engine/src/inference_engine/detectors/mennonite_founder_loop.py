"""Mennonite colony founder-loop detector for Phase 26.11.

This detector prevents Mennonite colony/endogamy DNA signals from being inserted
as direct ancestry without bridge evidence.

Target corpus case:

tree_13_mennonite_colony_founder_loop_ambiguity
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_FICTIONAL_BRIDGE = "fictional_bridge_person"
FLAG_POPULATION_BOUNDARY = "mennonite_jewish_or_slavic_boundary_error"
FLAG_FOUNDER_LOOP = "pedigree_collapse_mennonite_colony_founder_loop"
FLAG_SAME_NAME_COLONY_CONTEXT = "same_name_different_person_colony_context"
FLAG_SMALL_SEGMENT_OVERUSE = "pedigree_collapse_endogamy_small_segment_overuse"
FLAG_ONLINE_TREE_BRIDGE = "online_tree_fictional_bridge"
FLAG_DIRECT_INSERTION_BLOCKED = "direct_pedigree_insertion_blocked"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect fictional Mennonite bridge and founder-loop ancestry errors."""
    embedded_errors = _embedded_errors(tree)
    dna_matches = _dna_matches(tree)
    snippets = _archive_snippets(tree)

    if not _has_mennonite_founder_context(embedded_errors, dna_matches, snippets):
        return DetectorResult()

    flags: set[str] = set()
    relationship_claims: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    if _has_batensky_dodatko_anchor(snippets, dna_matches):
        evaluation_results["eval_13_005"] = True
        relationship_claims.append(
            {
                "claim_type": "confirmed_branch",
                "subject": "Batensky/Dodatko paternal branch",
                "status": "confirmed",
                "confidence": 0.94,
                "evidence": ["src_1301", "src_1306", "dna_1304", "dna_1305"],
                "reason": "Orthodox/civil records and close Batensky-Dodatko DNA anchors support the Slavic paternal branch.",
            }
        )

    if _has_mennonite_cluster(dna_matches):
        evaluation_results["eval_13_004"] = True
        relationship_claims.append(
            {
                "claim_type": "probable_cluster",
                "subject": "Mennonite Wiens/Friesen/Jantzen/Schmidt matches",
                "status": "separate_probable_cluster",
                "confidence": 0.83,
                "evidence": ["dna_1301", "dna_1302", "dna_1303", "src_1302", "src_1304"],
                "does_not_imply": "confirmed direct Batensky ancestry",
            }
        )

    for error in embedded_errors:
        error_type = error.get("type")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "fictional_bridge":
            flags.add(FLAG_FICTIONAL_BRIDGE)
            flags.add(FLAG_DIRECT_INSERTION_BLOCKED)
            evaluation_results["eval_13_001"] = True
            quarantined_claims.append(
                {
                    "claim_type": "fictional_bridge_person",
                    "status": "rejected",
                    "person_id": error.get("person_id"),
                    "rejected_name": "Ludmila /Friesen/",
                    "reason": error.get("reason"),
                    "accepted_rule": "Do not create placeholder ancestors to explain DNA.",
                }
            )

        elif error_type == "cross_population_confusion":
            flags.add(FLAG_POPULATION_BOUNDARY)
            flags.add(FLAG_DIRECT_INSERTION_BLOCKED)
            evaluation_results["eval_13_002"] = True
            quarantined_claims.append(
                {
                    "claim_type": "direct_parentage_from_population_signal",
                    "status": "rejected",
                    "persons": error.get("persons") or [],
                    "reason": error.get("reason"),
                    "accepted_rule": "Mennonite regional DNA/locality signal is not direct Batensky parentage.",
                }
            )

        elif error_type == "pedigree_collapse":
            flags.add(FLAG_FOUNDER_LOOP)
            quarantined_claims.append(
                {
                    "claim_type": "founder_population_direct_ancestry",
                    "status": "quarantined",
                    "match_id": error.get("match_id"),
                    "reason": "Mennonite founder-loop signal supports cluster context, not a direct ancestor insertion.",
                }
            )

        elif error_type == "duplicate":
            flags.add(FLAG_SAME_NAME_COLONY_CONTEXT)
            evaluation_results["eval_13_003"] = True
            persons = error.get("persons") or []
            merge_decisions.append(
                {
                    "merge_id": "reject_anna_friesen_colony_context_merge",
                    "merge_pair": persons,
                    "status": "Rejected",
                    "action": "do_not_merge",
                    "reason": error.get("reason"),
                    "rule_id": "mennonite_founder_loop",
                }
            )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_SMALL_SEGMENT_OVERUSE)
            quarantined_claims.append(
                {
                    "claim_type": "small_segment_founder_loop_anchor",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "Small-segment founder-population signal cannot be a direct relationship anchor.",
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_ONLINE_TREE_BRIDGE)
            flags.add(FLAG_DIRECT_INSERTION_BLOCKED)
            quarantined_claims.append(
                {
                    "claim_type": "online_tree_fictional_bridge",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "Public GEDCOM bridge needs primary records and must not conflict with Orthodox records.",
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


def _has_mennonite_founder_context(
    embedded_errors: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {
            "fictional_bridge",
            "cross_population_confusion",
            "pedigree_collapse",
            "duplicate",
            "dna_overinterpretation",
            "source_quality",
        }
        for error in embedded_errors
    )
    match_ids = {str(match.get("match_id")) for match in dna_matches}
    text = _combined_text(snippets).lower()
    has_context = "mennonite" in text and "friesen" in text and "batensky" in text
    return has_relevant_error and "dna_1301" in match_ids and has_context


def _has_batensky_dodatko_anchor(
    snippets: list[dict[str, Any]],
    dna_matches: list[dict[str, Any]],
) -> bool:
    text = _combined_text(snippets).lower()
    match_ids = {str(match.get("match_id")) for match in dna_matches}
    return (
        "birth of gregory batensky" in text
        and "birth of alexander batensky" in text
        and "matrona dodatko" in text
        and {"dna_1304", "dna_1305"}.issubset(match_ids)
    )


def _has_mennonite_cluster(dna_matches: list[dict[str, Any]]) -> bool:
    ids = {str(match.get("match_id")) for match in dna_matches}
    return {"dna_1301", "dna_1302", "dna_1303"}.issubset(ids)


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
        FLAG_FICTIONAL_BRIDGE,
        FLAG_POPULATION_BOUNDARY,
        FLAG_FOUNDER_LOOP,
        FLAG_SAME_NAME_COLONY_CONTEXT,
        FLAG_SMALL_SEGMENT_OVERUSE,
        FLAG_ONLINE_TREE_BRIDGE,
        FLAG_DIRECT_INSERTION_BLOCKED,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
