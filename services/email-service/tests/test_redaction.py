"""Тесты redaction-логики (allowlist + DNA hard rule)."""

from __future__ import annotations

import pytest
from email_service.services.redaction import redact_email_params


def test_allowlist_keys_passthrough() -> None:
    out = redact_email_params(
        {
            "amount_cents": 999,
            "currency": "usd",
            "plan_name": "Pro",
            "tree_name": "Demo Tree",
        }
    )
    assert out == {
        "amount_cents": 999,
        "currency": "usd",
        "plan_name": "Pro",
        "tree_name": "Demo Tree",
    }


def test_unknown_key_is_redacted() -> None:
    out = redact_email_params({"random_pii_field": "John Smith"})
    assert out == {"random_pii_field": "[redacted]"}


def test_dna_substrings_are_redacted_even_in_allowlist_form() -> None:
    """DNA wins over allowlist — каждый из этих ключей помечен как DNA."""
    cases = [
        "dna_match_count",
        "kit_id",
        "shared_cm",
        "total_cm",
        "rsid_list",
        "haplotype",
        "snp_count",
        "chromosome_painting",
        "genotype",
        "centimorgan_total",
    ]
    for key in cases:
        out = redact_email_params({key: 42})
        assert out[key] == "[redacted]", f"DNA key {key!r} should be redacted"


def test_case_insensitive_dna_match() -> None:
    out = redact_email_params({"DNA_FIELD": "x", "Kit_Summary": "y"})
    assert out["DNA_FIELD"] == "[redacted]"
    assert out["Kit_Summary"] == "[redacted]"


def test_redaction_does_not_mutate_input() -> None:
    original = {"amount_cents": 100, "leak": "x"}
    snapshot = original.copy()
    redact_email_params(original)
    assert original == snapshot


@pytest.mark.parametrize("safe_key", ["amount_cents", "plan_name", "invoice_url"])
def test_known_safe_keys_keep_their_values(safe_key: str) -> None:
    payload = {safe_key: "value"}
    out = redact_email_params(payload)
    assert out[safe_key] == "value"
