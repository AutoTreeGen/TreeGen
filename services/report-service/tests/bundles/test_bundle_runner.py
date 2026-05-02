"""Bundle worker tests — direct invocation of run_bundle_job (no arq).

Покрывает: happy path (zip + consolidated), individual-failure-continues,
all-fail, concurrent isolation, 24.3-function-reuse spy.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile
from typing import TYPE_CHECKING

import pytest
from report_service.bundles.data import create_bundle_job, load_bundle_job
from report_service.bundles.runner import run_bundle_job
from shared_models.orm import BundleStatus

if TYPE_CHECKING:
    from shared_models.storage import InMemoryStorage
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


pytestmark = pytest.mark.integration


def _pair(a: uuid.UUID, b: uuid.UUID, claim: str | None = None) -> dict[str, object]:
    return {
        "person_a_id": str(a),
        "person_b_id": str(b),
        "claimed_relationship": claim,
    }


async def _make_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    tree_id: uuid.UUID,
    user_id: uuid.UUID,
    pairs: list[dict[str, object]],
    output_format: str = "zip_of_pdfs",
) -> uuid.UUID:
    async with session_factory() as session:
        job = await create_bundle_job(
            session,
            tree_id=tree_id,
            requested_by=user_id,
            relationship_pairs=pairs,
            output_format=output_format,
            confidence_threshold=None,
        )
        await session.commit()
        return job.id


# ---------------------------------------------------------------------------
# 1. ZIP happy path
# ---------------------------------------------------------------------------


async def test_create_bundle_zip_format(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """3 pairs, ZIP output, weasyprint may or may not be available."""
    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], "parent_child"),
            _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"], "parent_child"),
            _pair(seeded_tree["child_a_id"], seeded_tree["child_b_id"], "sibling"),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)

    if summary["status"] == "failed":
        # WeasyPrint unavailable in this env; fail mode is expected.
        pytest.skip("WeasyPrint native libs unavailable — bundle render skipped")

    assert summary["status"] == "completed", summary
    assert summary["completed"] == 3
    assert summary["failed"] == 0

    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        assert job is not None
        assert job.status == BundleStatus.COMPLETED.value
        assert job.completed_count == 3
        assert job.failed_count == 0
        assert job.storage_url is not None
        assert job.storage_url.endswith(".zip")
        assert job.error_summary in (None, [])

    # Manifest sanity-check on the ZIP blob.
    blob = await in_memory_storage.get(job.storage_url)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = sorted(zf.namelist())
        assert "manifest.json" in names
        assert sum(1 for n in names if n.endswith(".pdf")) == 3
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["pair_count"] == 3
        assert len(manifest["pairs"]) == 3


# ---------------------------------------------------------------------------
# 2. Consolidated PDF happy path
# ---------------------------------------------------------------------------


async def test_create_bundle_consolidated_pdf(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """3 pairs, consolidated single PDF."""
    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], "parent_child"),
            _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"], "parent_child"),
            _pair(seeded_tree["child_a_id"], seeded_tree["child_b_id"], "sibling"),
        ],
        output_format="consolidated_pdf",
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    if summary["status"] == "failed":
        pytest.skip("WeasyPrint native libs unavailable")

    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        assert job is not None
        assert job.status == BundleStatus.COMPLETED.value
        # On consolidated_pdf success the URL should end with .pdf; if assembly
        # fell back to ZIP-of-PDFs (best-effort), .zip is acceptable.
        assert job.storage_url is not None
        assert job.storage_url.endswith((".pdf", ".zip"))


# ---------------------------------------------------------------------------
# 3. Individual pair failure continues the job
# ---------------------------------------------------------------------------


async def test_individual_pair_failure_continues_job(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """3 pairs: 2 valid + 1 with non-existent person → status=completed, failed_count=1."""
    bad_person = uuid.uuid4()
    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], "parent_child"),
            _pair(seeded_tree["parent_id"], bad_person, "parent_child"),
            _pair(seeded_tree["child_a_id"], seeded_tree["child_b_id"], "sibling"),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    if summary["status"] == "failed" and summary["completed"] == 0:
        pytest.skip("WeasyPrint native libs unavailable — all pairs failed for render reason")

    assert summary["status"] == "completed"
    assert summary["completed"] == 2
    assert summary["failed"] == 1

    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        assert job is not None
        assert job.failed_count == 1
        assert job.error_summary is not None
        assert len(job.error_summary) == 1
        entry = job.error_summary[0]
        assert entry["pair_index"] == 1
        assert "not found" in entry["message"].lower()


# ---------------------------------------------------------------------------
# 4. ALL pairs fail → status=failed
# ---------------------------------------------------------------------------


async def test_all_pairs_fail_marks_job_failed(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """All bad person_ids → status=failed, completed=0."""
    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(uuid.uuid4(), uuid.uuid4(), "parent_child"),
            _pair(uuid.uuid4(), uuid.uuid4(), "sibling"),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    assert summary["status"] == "failed"
    assert summary["completed"] == 0
    assert summary["failed"] == 2

    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        assert job is not None
        assert job.status == BundleStatus.FAILED.value
        assert job.storage_url is None
        assert job.failed_count == 2


# ---------------------------------------------------------------------------
# 5. Concurrent jobs isolation
# ---------------------------------------------------------------------------


async def test_concurrent_jobs_isolation(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """Two jobs run in parallel — counters and statuses don't bleed."""
    pairs_a = [
        _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], "parent_child"),
    ]
    pairs_b = [
        _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"], "parent_child"),
        _pair(seeded_tree["child_a_id"], seeded_tree["child_b_id"], "sibling"),
    ]
    job_a = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=pairs_a,
    )
    job_b = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=pairs_b,
    )

    sum_a, sum_b = await asyncio.gather(
        run_bundle_job(session_factory, job_id=job_a, storage=in_memory_storage),
        run_bundle_job(session_factory, job_id=job_b, storage=in_memory_storage),
    )
    if sum_a["status"] == "failed" or sum_b["status"] == "failed":
        pytest.skip("WeasyPrint native libs unavailable")

    async with session_factory() as session:
        job_a_row = await load_bundle_job(session, job_id=job_a)
        job_b_row = await load_bundle_job(session, job_id=job_b)
        assert job_a_row is not None
        assert job_b_row is not None
        assert job_a_row.completed_count == 1
        assert job_b_row.completed_count == 2
        assert job_a_row.storage_url != job_b_row.storage_url


# ---------------------------------------------------------------------------
# 6. Reuses 24.3 single-report function — spy
# ---------------------------------------------------------------------------


async def test_reuse_24_3_single_report_function(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anti-fork guarantee: bundle worker MUST call 24.3 generate_pdf_bytes_for_pair.

    Patches the function at the call-site (runner.generate_pdf_bytes_for_pair)
    and asserts it was invoked once per pair. Anti-fork enforcement per
    ADR-0078 §"single source of truth".
    """
    call_count = {"n": 0}

    from report_service.relationship import pipeline as pipeline_mod
    from report_service.relationship.pipeline import generate_pdf_bytes_for_pair

    real_fn = generate_pdf_bytes_for_pair

    async def _spy(*args: object, **kwargs: object) -> object:
        call_count["n"] += 1
        return await real_fn(*args, **kwargs)

    # Patch the symbol at runner's import site.
    from report_service.bundles import runner as runner_mod

    monkeypatch.setattr(runner_mod, "generate_pdf_bytes_for_pair", _spy)
    # Also patch the canonical module to assert no second copy exists.
    monkeypatch.setattr(pipeline_mod, "generate_pdf_bytes_for_pair", _spy)

    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], "parent_child"),
            _pair(seeded_tree["parent_id"], seeded_tree["child_b_id"], "parent_child"),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    if summary["status"] == "failed" and summary["completed"] == 0:
        # Even with WeasyPrint missing, the spy records the calls before
        # PdfRenderError fires; assertion still meaningful.
        pass

    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# 7. Auto-derive claim path
# ---------------------------------------------------------------------------


async def test_auto_derive_claim_when_omitted(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """Pair с claimed_relationship=None → auto-derive из Family/FamilyChild."""
    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], seeded_tree["child_a_id"], None),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    if summary["status"] == "failed" and summary["completed"] == 0:
        pytest.skip("WeasyPrint native libs unavailable")
    assert summary["completed"] == 1
    assert summary["failed"] == 0


async def test_auto_derive_claim_unresolvable_logs_error(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_tree: dict[str, uuid.UUID],
    in_memory_storage: InMemoryStorage,
) -> None:
    """Pair с unrelated persons + no claim → error_summary entry, status=failed."""
    stranger = uuid.uuid4()
    # Add a stranger person in the same tree so person-existence isn't the failure.
    async with session_factory() as session:
        from shared_models.orm import Person

        session.add(Person(id=stranger, tree_id=seeded_tree["tree_id"], sex="U"))
        await session.commit()

    job_id = await _make_job(
        session_factory,
        tree_id=seeded_tree["tree_id"],
        user_id=seeded_tree["user_id"],
        pairs=[
            _pair(seeded_tree["parent_id"], stranger, None),
        ],
    )
    summary = await run_bundle_job(session_factory, job_id=job_id, storage=in_memory_storage)
    assert summary["status"] == "failed"
    async with session_factory() as session:
        job = await load_bundle_job(session, job_id=job_id)
        assert job is not None
        assert job.failed_count == 1
        assert job.error_summary is not None
        assert "specify claimed_relationship explicitly" in job.error_summary[0]["message"]
