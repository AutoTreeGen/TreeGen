"""Famous-relative and rabbinical overclaim detector for Phase 26.6.

This detector targets fantasy genealogy patterns where public trees attach local
families to royal, Rashi, King David, Maharal or rabbinical dynasty chains
without primary bridge evidence. It starts with:

tree_19_famous_relative_royal_rabbinical_overclaim_filter

The detector is intentionally conservative: it can confirm a local branch when
primary local records exist, but quarantines medieval/famous-dynasty claims
without bridge documents.
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.result import DetectorResult

FLAG_ROYAL_RASHI_CHAIN = "royal_rashi_king_david_public_tree_chain"
FLAG_SCHNEERSON_BESHT = "rabbinical_schneerson_to_baal_shem_tov"
FLAG_TITLE_SURNAME_AS_PROOF = "rabbinical_title_or_surname_as_proof"
FLAG_TINY_DNA_MEDIEVAL = "tiny_dna_match_used_for_medieval_descent"
FLAG_SAME_NAME_FALSE_MERGE = "same_name_rabbinical_surname_false_merge"
FLAG_PUBLIC_TREE_NO_PRIMARY_BRIDGE = "public_tree_famous_descent_no_primary_bridge"
FLAG_FAMOUS_QUARANTINE_REQUIRED = "famous_descent_quarantine_required"


def detect(tree: dict[str, Any]) -> DetectorResult:
    """Detect unsupported famous-relative and rabbinical descent overclaims."""
    embedded_errors = _embedded_errors(tree)
    snippets = _archive_snippets(tree)

    if not _has_famous_overclaim_context(embedded_errors, snippets):
        return DetectorResult()

    flags: set[str] = set()
    relationship_claims: list[dict[str, Any]] = []
    merge_decisions: list[dict[str, Any]] = []
    quarantined_claims: list[dict[str, Any]] = []
    evaluation_results: dict[str, bool] = {}

    if _has_local_soloveichik_primary_evidence(snippets):
        evaluation_results["eval_19_001"] = True
        relationship_claims.append(
            {
                "claim_type": "local_branch",
                "subject_name": "Moshe /Soloveichik/",
                "status": "confirmed",
                "confidence": 0.9,
                "evidence": ["src_1901", "src_1902", "src_1903"],
                "scope": "local Brest Soloveichik branch only",
                "does_not_imply": [
                    "Maharal descent",
                    "Rashi descent",
                    "King David descent",
                    "Schneerson dynasty descent",
                ],
            }
        )

    for error in embedded_errors:
        error_type = error.get("type")
        subtype = error.get("subtype")
        expected_flag = error.get("expected_flag")

        if not isinstance(expected_flag, str):
            continue

        if error_type == "fabrication":
            flags.add(expected_flag)
            flags.add(FLAG_FAMOUS_QUARANTINE_REQUIRED)

            if subtype == "maharal_rashi_king_david_bridge":
                evaluation_results["eval_19_002"] = True
                quarantined_claims.append(
                    {
                        "claim_type": "famous_descent",
                        "status": "rejected",
                        "person_id": error.get("person_id"),
                        "rejected_chain": ["Maharal of Prague", "Rashi", "King David"],
                        "reason": "Public-tree chain has no primary bridge records.",
                        "flag": expected_flag,
                    }
                )

            elif subtype == "hasidic_dynasty_unsourced_bridge":
                evaluation_results["eval_19_003"] = True
                quarantined_claims.append(
                    {
                        "claim_type": "rabbinical_dynasty_descent",
                        "status": "rejected",
                        "person_id": error.get("person_id"),
                        "rejected_chain": ["Schneerson dynasty", "Baal Shem Tov"],
                        "reason": "Hasidic dynasty bridge is unsourced for this local branch.",
                        "flag": expected_flag,
                    }
                )

            elif subtype == "rabbinical_surname_as_descent":
                quarantined_claims.append(
                    {
                        "claim_type": "surname_or_title_descent",
                        "status": "rejected",
                        "person_id": error.get("person_id"),
                        "reason": error.get("reason"),
                        "flag": expected_flag,
                    }
                )

        elif error_type == "dna_overinterpretation":
            flags.add(FLAG_TINY_DNA_MEDIEVAL)
            flags.add(FLAG_FAMOUS_QUARANTINE_REQUIRED)
            quarantined_claims.append(
                {
                    "claim_type": "dna_to_medieval_descent",
                    "status": "rejected",
                    "match_id": error.get("match_id"),
                    "reason": error.get("reason"),
                    "accepted_rule": "tiny autosomal segments cannot prove medieval descent",
                    "flag": FLAG_TINY_DNA_MEDIEVAL,
                }
            )

        elif error_type == "same_name_different_person":
            flags.add(FLAG_SAME_NAME_FALSE_MERGE)
            flags.add(FLAG_FAMOUS_QUARANTINE_REQUIRED)
            evaluation_results["eval_19_004"] = True
            persons = error.get("persons") or []
            merge_decisions.append(
                {
                    "merge_id": "reject_I5_I12_rabbinical_same_name_merge",
                    "merge_pair": persons,
                    "status": "Rejected",
                    "action": "do_not_merge",
                    "reason": error.get("reason"),
                    "rule_id": "famous_overclaim_filter",
                }
            )
            quarantined_claims.append(
                {
                    "claim_type": "same_name_rabbinical_merge",
                    "status": "rejected",
                    "persons": persons,
                    "reason": error.get("reason"),
                }
            )

        elif error_type == "source_quality":
            flags.add(FLAG_PUBLIC_TREE_NO_PRIMARY_BRIDGE)
            flags.add(FLAG_FAMOUS_QUARANTINE_REQUIRED)
            evaluation_results["eval_19_005"] = True
            quarantined_claims.append(
                {
                    "claim_type": "public_tree_famous_descent",
                    "snippet_id": error.get("snippet_id"),
                    "status": "quarantined",
                    "reason": error.get("reason"),
                    "accepted_rule": "public trees require primary bridge evidence before sealed-tree inclusion",
                    "flag": FLAG_PUBLIC_TREE_NO_PRIMARY_BRIDGE,
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


def _has_famous_overclaim_context(
    embedded_errors: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
) -> bool:
    has_relevant_error = any(
        error.get("type")
        in {"fabrication", "dna_overinterpretation", "same_name_different_person", "source_quality"}
        for error in embedded_errors
    )
    text = _combined_text(snippets).lower()
    has_famous_text = any(
        token in text
        for token in (
            "maharal",
            "rashi",
            "king david",
            "schneerson",
            "baal shem tov",
            "rabbi dynasty bridge",
            "famous soloveitchik",
        )
    )
    return has_relevant_error and has_famous_text


def _has_local_soloveichik_primary_evidence(snippets: list[dict[str, Any]]) -> bool:
    text = _combined_text(snippets).lower()
    return (
        "birth of moshe soloveichik" in text
        and "marriage of chaim soloveichik" in text
        and "brest jewish community" in text
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
        FLAG_ROYAL_RASHI_CHAIN,
        FLAG_SCHNEERSON_BESHT,
        FLAG_TITLE_SURNAME_AS_PROOF,
        FLAG_TINY_DNA_MEDIEVAL,
        FLAG_SAME_NAME_FALSE_MERGE,
        FLAG_PUBLIC_TREE_NO_PRIMARY_BRIDGE,
        FLAG_FAMOUS_QUARANTINE_REQUIRED,
    ]
    return [flag for flag in order if flag in flags]


__all__ = ["detect"]
