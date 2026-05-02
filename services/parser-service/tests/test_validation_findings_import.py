"""Phase 5.8 — integration test: import GED → validator findings persisted.

Verifies the full wire-up: import_runner calls validate_document() after
parse, and the resulting findings (Finding.to_dict()) land in
``import_jobs.validation_findings`` jsonb column for downstream review.

Mirrors ``test_run_import_persists_unknown_tags`` (Phase 5.5a) — same
runner-direct + sql-readback pattern.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from shared_models.orm import ImportJob
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Synthetic GED содержит TWO детектируемые проблемы, обе importable
# (не нарушают DB-constraints вроде ``uq_family_children_*``):
#  1. mother_age_low    — I2 born 1900, child I3 born 1910 → age 10
#  2. death_before_birth — I4 born 1900 / died 1850
#
# Note: duplicate_child / duplicate_spouse покрыты unit-тестами; здесь
# их нельзя проверить через import_runner, потому что DB-уникальность
# (``uq_family_children_family_id_child_person_id``) роняет import до
# персиста findings — это не баг validator'а, а правильное поведение
# importer'а (advisory validator не предотвращает hard-fail на corrupt
# data; users увидят findings в ``import_jobs.validation_findings`` ТОЛЬКО
# при успешном parse + bulk-insert).
_GED_WITH_KNOWN_ISSUES = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1895
1 FAMS @F1@
0 @I2@ INDI
1 NAME Mary /Jones/
1 SEX F
1 BIRT
2 DATE 1900
1 FAMS @F1@
0 @I3@ INDI
1 NAME Tim /Smith/
1 SEX M
1 BIRT
2 DATE 1910
1 FAMC @F1@
0 @I4@ INDI
1 NAME Backwards /Person/
1 SEX M
1 BIRT
2 DATE 1900
1 DEAT
2 DATE 1850
0 @F1@ FAM
1 HUSB @I1@
1 WIFE @I2@
1 CHIL @I3@
0 TRLR
"""


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str):
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _run_import_and_get_job(session_factory, ged_bytes: bytes) -> ImportJob:
    """Helper: call run_import directly, return ImportJob row from DB."""
    from parser_service.config import get_settings
    from parser_service.services.import_runner import run_import

    with tempfile.NamedTemporaryFile(delete=False, suffix=".ged") as tmp:
        tmp.write(ged_bytes)
        tmp_path = Path(tmp.name)

    try:
        async with session_factory() as session:
            job = await run_import(
                session,
                tmp_path,
                owner_email=get_settings().owner_email,
                tree_name="phase-5-8-validator-test",
                source_filename="phase-5-8.ged",
            )
            await session.commit()
            job_id = job.id

        # Re-read fresh from DB to confirm persistence (avoid in-memory stale).
        async with session_factory() as session:
            return (
                await session.execute(select(ImportJob).where(ImportJob.id == job_id))
            ).scalar_one()
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_validation_findings_persisted_with_known_issues(session_factory) -> None:
    """Import a GED with seeded issues; assert findings persisted by rule_id."""
    job = await _run_import_and_get_job(session_factory, _GED_WITH_KNOWN_ISSUES)

    findings = job.validation_findings
    assert isinstance(findings, list)
    assert len(findings) > 0, "expected findings on a GED with seeded issues"

    rule_ids = {f["rule_id"] for f in findings}
    # Both seeded issues should fire:
    assert "death_before_birth" in rule_ids, findings
    assert "mother_age_low" in rule_ids, findings

    # Each finding should have the structured shape.
    for f in findings:
        assert "rule_id" in f
        assert "severity" in f
        assert f["severity"] in ("info", "warning", "error")
        assert "message" in f
        assert "context" in f


@pytest.mark.asyncio
async def test_validation_findings_empty_on_clean_ged(session_factory) -> None:
    """Clean GED with no rule-violations → empty findings list."""
    clean_ged = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
1 BIRT
2 DATE 1900
1 DEAT
2 DATE 1980
0 TRLR
"""
    job = await _run_import_and_get_job(session_factory, clean_ged)
    assert job.validation_findings == []
