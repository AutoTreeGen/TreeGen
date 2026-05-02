"""Phase 5.8 — validator robustness on real-world GED corpus.

Per memory ``test_corpus_gedcom_files.md``: corpus lives at
``F:\\Projects\\GED`` (override via ``GEDCOM_TEST_CORPUS`` env var).
Tests auto-skip if the corpus is missing — works on owner's machine
locally, no-op in CI.

The contract for v1: validator MUST NOT crash on any real-world GED
file the parser can read. Specific finding counts vary by file content;
we don't assert exact numbers — only structural invariants (every
finding has a known rule_id and a non-empty message).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
from gedcom_parser import GedcomDocument, parse_file
from gedcom_parser.validator import (
    ValidatorContext,
    default_rules,
    validate_document,
)

pytestmark = pytest.mark.gedcom_real


def _corpus_path() -> Path | None:
    raw = os.environ.get("GEDCOM_TEST_CORPUS", "F:/Projects/GED")
    p = Path(raw)
    return p if p.exists() and p.is_dir() else None


def _ged_files(limit: int | None = None) -> list[Path]:
    root = _corpus_path()
    if root is None:
        return []
    files = sorted(p for p in root.iterdir() if p.suffix.lower() == ".ged")
    if limit is not None:
        files = files[:limit]
    return files


_KNOWN_RULE_IDS: frozenset[str] = frozenset(r.rule_id for r in default_rules()) | {
    # Sub-rule_ids emitted by parent classes (parent_age splits into _low/_high).
    "mother_age_low",
    "mother_age_high",
    "father_age_low",
    "father_age_high",
    # parent_alive splits per parent role.
    "child_born_after_mother_death",
    "child_born_long_after_father_death",
    # broken_xref splits per relationship kind.
    "broken_xref_fams",
    "broken_xref_famc",
    "broken_xref_husb",
    "broken_xref_wife",
    "broken_xref_chil",
    "broken_xref_note",
    "broken_xref_sour",
    "broken_xref_obje",
    "broken_xref_repo",
    "broken_xref_subm",
    "broken_xref_other",
    # Engine internal-error fallback.
    "validator_internal_error",
}


@pytest.mark.skipif(_corpus_path() is None, reason="GEDCOM_TEST_CORPUS not present")
def test_validator_never_crashes_on_real_corpus() -> None:
    """For every .ged in corpus: parse + validate produces well-formed findings.

    No exception escapes; every finding has a known rule_id and a
    non-empty message. We deliberately don't assert finding counts —
    they're data-dependent and would brittle the test against corpus
    refresh.
    """
    files = _ged_files()
    assert files, "corpus exists but contains no .ged files"

    examined = 0
    for ged_path in files:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                records, encoding = parse_file(ged_path)
            except Exception:
                # Parser failures are out of scope for the validator test;
                # parser has its own corpus suite. Skip un-parseable files.
                continue
            doc = GedcomDocument.from_records(records, encoding=encoding)

        ctx = ValidatorContext(raw_records=tuple(records))
        findings = validate_document(doc, ctx=ctx)
        for f in findings:
            assert f.rule_id in _KNOWN_RULE_IDS, f"{ged_path.name}: unknown rule_id {f.rule_id!r}"
            assert f.message, f"{ged_path.name}: finding has empty message"
        examined += 1

    assert examined > 0, "no .ged file was successfully parsed in the corpus"
