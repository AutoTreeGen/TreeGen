"""Golden test on real-world GEDCOM (Voikhansky-rich Ancestry.ged).

Skipped в CI (нет corpus). Локально: ``GEDCOM_TEST_CORPUS=F:/Projects/GED
uv run pytest -m gedcom_real``.

Per memory ``test_corpus_gedcom_files.md``: real GED-corpus в
``F:/Projects/GED``, не в default ``D:/Projects/GED`` из CLAUDE.md.

Brief требует at least one rule to fire on Voikhansky fixture (документированный
multi-researcher confirmed fabrication из ``dna_match_discovery_dashboard``
memory). Конкретные expected counts задаются как нижняя граница — если scan'у
найдут больше, тест по-прежнему проходит, mass-fabricated ledger всё равно
будет flagged.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import pytest
from gedcom_parser import parse_document_file
from gedcom_parser.fantasy import scan_document
from gedcom_parser.fantasy.types import FantasySeverity

pytestmark = pytest.mark.gedcom_real

_CORPUS = Path(os.environ.get("GEDCOM_TEST_CORPUS", "F:/Projects/GED"))
_VOIKHANSKY_FIXTURE = _CORPUS / "Ancestry.ged"


@pytest.fixture(scope="module")
def voikhansky_doc():  # type: ignore[no-untyped-def]
    """Распарсить fixture один раз на module."""
    if not _VOIKHANSKY_FIXTURE.exists():
        pytest.skip(f"corpus fixture not found: {_VOIKHANSKY_FIXTURE}")
    warnings.filterwarnings("ignore")
    return parse_document_file(_VOIKHANSKY_FIXTURE)


def test_full_scan_voikhansky_fixture_returns_at_least_one_flag(voikhansky_doc) -> None:  # type: ignore[no-untyped-def]
    """Brief golden requirement: scan должен поднять хотя бы один flag.

    Мы не assert'им конкретные counts — fixture обновляется между runs;
    важно что rule engine fires на real-world data, и что critical bugs
    не маскируют все flags.
    """
    flags = scan_document(voikhansky_doc)
    assert len(flags) > 0, "expected at least one flag on Voikhansky-rich fixture"


def test_voikhansky_fixture_has_multiple_severity_levels(voikhansky_doc) -> None:  # type: ignore[no-untyped-def]
    """Realistic fixture должен поднять flags нескольких severity-уровней."""
    flags = scan_document(voikhansky_doc)
    severities = {f.severity for f in flags}
    # Минимум 2 разных уровня — иначе rules слишком одинаково настроены.
    assert len(severities) >= 2, f"only one severity level: {severities}"


def test_voikhansky_fixture_critical_impossibilities_present(voikhansky_doc) -> None:  # type: ignore[no-untyped-def]
    """Real-world tree обычно содержит хотя бы один CRITICAL (death-before-birth).

    Brief specifically targets fabrications — Ancestry.ged owner-наблюдается
    как viral-fabrication-полный.
    """
    flags = scan_document(voikhansky_doc)
    critical_count = sum(1 for f in flags if f.severity is FantasySeverity.CRITICAL)
    assert critical_count > 0, "expected at least one CRITICAL flag on real-world fixture"


def test_voikhansky_fixture_no_internal_errors(voikhansky_doc) -> None:  # type: ignore[no-untyped-def]
    """Engine catches rule exceptions — но на realistic input их быть не должно.

    Если эта проверка падает, что-то fundamentally сломано в одном из rules.
    """
    flags = scan_document(voikhansky_doc)
    internal = [f for f in flags if f.rule_id == "fantasy_internal_error"]
    assert internal == [], f"unexpected internal errors: {[f.reason for f in internal]}"
