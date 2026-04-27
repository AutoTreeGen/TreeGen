"""Интеграционные тесты bulk hypothesis-compute (Phase 7.5).

Покрывают и HTTP-слой (compute_all / status / cancel endpoints), и
service-слой (cancel mid-flight, failed-job error capture). Сервисные
тесты идут напрямую через ``bulk_hypothesis_runner``, чтобы можно было
управлять lifecycle job'а тоньше, чем через sync-POST endpoint.

Marker: ``integration`` + ``db`` (нужен testcontainers-postgres из conftest).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _gen_50_person_ged() -> bytes:
    """Сгенерировать GED с 50 INDI-записями.

    25 пар «похожих» (Zhitnitzky/Zhytnicki кластер) — у них совпадает
    Daitch-Mokotoff bucket, year, place — dedup_finder выдаст по
    candidate-pair на каждую. Таким образом в test'ах будет ≥25
    кандидатов, что даёт нагрузку на bulk-loop, но всё ещё умещается
    в 5-секундный budget.

    Возвращает байты для multipart/form-data upload через ``/imports``.
    """
    lines: list[str] = [
        "0 HEAD",
        "1 SOUR test",
        "1 GEDC",
        "2 VERS 5.5.5",
        "2 FORM LINEAGE-LINKED",
        "1 CHAR UTF-8",
    ]
    surname_clusters = [
        ("Zhitnitzky", "Zhytnicki"),
        ("Kaplan", "Kapelan"),
        ("Levin", "Levine"),
        ("Goldberg", "Goldberger"),
        ("Rosenberg", "Rozenberg"),
        ("Schwartz", "Shvarts"),
        ("Friedman", "Fridman"),
        ("Cohen", "Kohen"),
        ("Berman", "Beerman"),
        ("Klein", "Kleyn"),
        ("Stein", "Shtein"),
        ("Weiss", "Vays"),
        ("Mendelsohn", "Mendelson"),
        ("Reisman", "Raisman"),
        ("Ostrovsky", "Ostrovskii"),
        ("Polonsky", "Polonskii"),
        ("Rabinowitz", "Rabinovich"),
        ("Tannenbaum", "Tanenbaum"),
        ("Shapiro", "Shapira"),
        ("Markowitz", "Markovich"),
        ("Greenspan", "Grinshpan"),
        ("Brodsky", "Brodskii"),
        ("Kaminsky", "Kaminskii"),
        ("Lubin", "Lyubin"),
        ("Galpern", "Halpern"),
    ]
    for idx, (a, b) in enumerate(surname_clusters, start=1):
        i_a = 2 * idx - 1
        i_b = 2 * idx
        lines.extend(
            [
                f"0 @I{i_a}@ INDI",
                f"1 NAME Meir /{a}/",
                "1 SEX M",
                "1 BIRT",
                "2 DATE 1850",
                "2 PLAC Slonim, Grodno, Russian Empire",
                f"0 @I{i_b}@ INDI",
                f"1 NAME Meir /{b}/",
                "1 SEX M",
                "1 BIRT",
                "2 DATE 1850",
                "2 PLAC Slonim",
            ]
        )
    lines.append("0 TRLR")
    return ("\n".join(lines) + "\n").encode("utf-8")


_GED_50 = _gen_50_person_ged()


async def _import_ged(app_client, ged_bytes: bytes) -> uuid.UUID:
    """Импортировать GED через `POST /imports` и вернуть tree_id."""
    files = {"file": ("test.ged", ged_bytes, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["tree_id"])


async def _open_session(
    postgres_dsn: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Поднять async engine + session-factory для прямых service-call'ов.

    Возвращает оба, чтобы тест мог сделать ``await engine.dispose()``
    в finally — testcontainer не обязан собирать соединения сам.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_dsn, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


# ---------------------------------------------------------------------------
# HTTP-level tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compute_all_returns_201_with_progress(app_client) -> None:
    """POST /compute-all создаёт job, отдаёт SUCCEEDED + progress."""
    tree_id = await _import_ged(app_client, _GED_50)

    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["tree_id"] == str(tree_id)
    # Sync-режим: к моменту response job должен быть terminal.
    assert body["status"] in ("succeeded", "failed", "cancelled")
    assert body["progress"]["total"] >= 1, "ожидаем хотя бы одну candidate-pair на 50-INDI"
    assert body["progress"]["processed"] == body["progress"]["total"]
    assert body["progress"]["hypotheses_created"] >= 1


@pytest.mark.asyncio
async def test_compute_all_50_persons_completes_under_5s(app_client) -> None:
    """Performance budget: 50 INDI → succeeded < 5s wall-clock.

    Источник 5s — brief Phase 7.5 («50-person tree, all rules → completes
    < 5 seconds»). Если упадёт — либо bulk-runner не batched, либо
    dedup_finder регрессировал по сложности.
    """
    tree_id = await _import_ged(app_client, _GED_50)

    started = time.perf_counter()
    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "succeeded"
    assert elapsed < 5.0, f"compute-all для 50 INDI заняло {elapsed:.2f}s (budget=5.0s)"


@pytest.mark.asyncio
async def test_idempotency_within_one_hour(app_client) -> None:
    """Повторный POST в течение часа возвращает тот же job_id."""
    tree_id = await _import_ged(app_client, _GED_50)

    first = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]

    second = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first_id, (
        "idempotency: вторая POST в течение 1h должна вернуть тот же job"
    )
    # Прогресс не должен сброситься у уже завершённого job'а.
    assert second.json()["status"] == first.json()["status"]
    assert second.json()["progress"]["processed"] == first.json()["progress"]["processed"]


@pytest.mark.asyncio
async def test_get_status_returns_job_state(app_client) -> None:
    """GET /compute-jobs/{id} возвращает текущее состояние."""
    tree_id = await _import_ged(app_client, _GED_50)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    job_id = created.json()["id"]

    status_resp = await app_client.get(f"/trees/{tree_id}/hypotheses/compute-jobs/{job_id}")
    assert status_resp.status_code == 200, status_resp.text
    assert status_resp.json()["id"] == job_id
    assert status_resp.json()["tree_id"] == str(tree_id)


@pytest.mark.asyncio
async def test_get_status_404_for_unknown_job(app_client) -> None:
    """Чужой/несуществующий job_id → 404."""
    tree_id = await _import_ged(app_client, _GED_50)
    response = await app_client.get(f"/trees/{tree_id}/hypotheses/compute-jobs/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_status_404_when_job_in_other_tree(app_client) -> None:
    """job_id корректный, но из другого дерева → 404 (no info-leak)."""
    tree_a = await _import_ged(app_client, _GED_50)
    tree_b = await _import_ged(app_client, _GED_50)
    created = await app_client.post(
        f"/trees/{tree_a}/hypotheses/compute-all",
        json={},
    )
    job_id = created.json()["id"]

    response = await app_client.get(f"/trees/{tree_b}/hypotheses/compute-jobs/{job_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_cancel_sets_flag_for_finished_job(app_client) -> None:
    """PATCH /cancel на терминальном job — no-op, 200 + текущий state.

    Sync-mode → к моменту cancel'а job уже SUCCEEDED. Это валидный
    кейс: UI кликнул cancel, но запрос пришёл после завершения. Ожидаем
    200 без флипа в CANCELLED (статус уже терминальный).
    """
    tree_id = await _import_ged(app_client, _GED_50)
    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={},
    )
    job_id = created.json()["id"]
    assert created.json()["status"] == "succeeded"

    cancel = await app_client.patch(f"/hypotheses/compute-jobs/{job_id}/cancel")
    assert cancel.status_code == 200, cancel.text
    body = cancel.json()
    assert body["id"] == job_id
    # Уже succeeded — cancel-runner не сбрасывает status.
    assert body["status"] == "succeeded"


@pytest.mark.asyncio
async def test_patch_cancel_404_for_unknown_job(app_client) -> None:
    response = await app_client.patch(f"/hypotheses/compute-jobs/{uuid.uuid4()}/cancel")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Service-level tests (bulk_hypothesis_runner direct calls).
# Нужны там, где HTTP-sync не позволяет наблюдать промежуточные состояния
# (cancel mid-flight, induced exception в compute_hypothesis).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_mid_flight_via_service(app_client, postgres_dsn) -> None:
    """Cancel срабатывает между batch'ами.

    Запускаем execute в одной задаче, во второй — выставляем
    cancel_requested. Подменяем compute_hypothesis на slow-stub
    (asyncio.sleep), чтобы execute не успел проскочить все pairs за один
    batch. Ожидаем status='cancelled' и progress.processed < total.
    """
    from parser_service.services import bulk_hypothesis_runner
    from parser_service.services.bulk_hypothesis_runner import (
        cancel_compute_job,
        enqueue_compute_job,
        execute_compute_job,
    )

    tree_id = await _import_ged(app_client, _GED_50)

    # Slow-stub: каждая compute занимает 50ms → batch=2 даст ~100ms
    # между cancel-check'ами. Ждём ~150ms перед cancel — точно попадём
    # в середину loop'а.
    async def _slow_compute(session, tree_id, a_id, b_id, hypothesis_type):  # noqa: ARG001
        # «не создалась гипотеза» — None implicit, для теста этого достаточно.
        await asyncio.sleep(0.05)

    original_compute = bulk_hypothesis_runner.compute_hypothesis
    bulk_hypothesis_runner.compute_hypothesis = _slow_compute  # type: ignore[assignment]

    engine, SessionMaker = await _open_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as enq_session:
            job = await enqueue_compute_job(enq_session, tree_id)
            await enq_session.commit()
            job_id = job.id

        async def _runner() -> None:
            async with SessionMaker() as run_session:
                # batch_size=2 → cancel-check каждые 2 pair'а.
                await execute_compute_job(run_session, job_id, batch_size=2)

        runner_task = asyncio.create_task(_runner())
        await asyncio.sleep(0.15)  # дождаться, что execute начал loop

        async with SessionMaker() as cancel_session:
            await cancel_compute_job(cancel_session, job_id)
            await cancel_session.commit()

        await runner_task

        async with SessionMaker() as check_session:
            from shared_models.orm import HypothesisComputeJob
            from sqlalchemy import select

            final = (
                await check_session.execute(
                    select(HypothesisComputeJob).where(HypothesisComputeJob.id == job_id)
                )
            ).scalar_one()
            assert final.status == "cancelled", (
                f"expected status=cancelled, got {final.status}; progress={final.progress}"
            )
            assert final.progress["processed"] < final.progress["total"], (
                "cancel mid-flight должен оставлять processed < total"
            )
            assert final.finished_at is not None
    finally:
        bulk_hypothesis_runner.compute_hypothesis = original_compute  # type: ignore[assignment]
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_job_records_error(app_client, postgres_dsn) -> None:
    """Exception в compute_hypothesis → status=failed + error заполнен."""
    from parser_service.services import bulk_hypothesis_runner
    from parser_service.services.bulk_hypothesis_runner import (
        enqueue_compute_job,
        execute_compute_job,
    )

    tree_id = await _import_ged(app_client, _GED_50)

    async def _broken_compute(session, tree_id, a_id, b_id, hypothesis_type):  # noqa: ARG001
        msg = "synthetic boom for failure path"
        raise RuntimeError(msg)

    original_compute = bulk_hypothesis_runner.compute_hypothesis
    bulk_hypothesis_runner.compute_hypothesis = _broken_compute  # type: ignore[assignment]

    engine, SessionMaker = await _open_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as enq_session:
            job = await enqueue_compute_job(enq_session, tree_id)
            await enq_session.commit()
            job_id = job.id

        async with SessionMaker() as run_session:
            with pytest.raises(RuntimeError, match="synthetic boom"):
                await execute_compute_job(run_session, job_id)

        async with SessionMaker() as check_session:
            from shared_models.orm import HypothesisComputeJob
            from sqlalchemy import select

            final = (
                await check_session.execute(
                    select(HypothesisComputeJob).where(HypothesisComputeJob.id == job_id)
                )
            ).scalar_one()
            assert final.status == "failed"
            assert final.error is not None
            assert "synthetic boom" in final.error
            assert final.finished_at is not None
    finally:
        bulk_hypothesis_runner.compute_hypothesis = original_compute  # type: ignore[assignment]
        await engine.dispose()


@pytest.mark.asyncio
async def test_rule_ids_persisted_in_job_row(app_client, postgres_dsn) -> None:
    """``rule_ids`` сохраняется как jsonb для audit (currently informational).

    Полная фильтрация — отдельный follow-up PR. Этот тест защищает round-trip
    через API → ORM → response, чтобы forward-compatible не сломался.
    """
    tree_id = await _import_ged(app_client, _GED_50)

    response = await app_client.post(
        f"/trees/{tree_id}/hypotheses/compute-all",
        json={"rule_ids": ["surname_dm_match", "birth_year_match"]},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["rule_ids"] == ["surname_dm_match", "birth_year_match"]

    # Verify сохраняется в БД, а не только в response.
    engine, SessionMaker = await _open_session(postgres_dsn)  # noqa: N806
    try:
        async with SessionMaker() as session:
            from shared_models.orm import HypothesisComputeJob
            from sqlalchemy import select

            stored = (
                await session.execute(
                    select(HypothesisComputeJob).where(
                        HypothesisComputeJob.id == uuid.UUID(body["id"])
                    )
                )
            ).scalar_one()
            assert stored.rule_ids == ["surname_dm_match", "birth_year_match"]
    finally:
        await engine.dispose()
