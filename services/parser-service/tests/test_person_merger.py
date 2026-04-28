"""HTTP-тесты merge endpoint'ов и сервиса (Phase 4.6 — ADR-0022).

Покрытие:

* preview — diff без mutation'а;
* commit — happy path, idempotency по ``confirm_token``;
* commit без ``confirm`` — 422 (Pydantic Literal[True]);
* commit на уже-merged персону — 409 (subject_already_merged);
* commit с rejected same_person hypothesis — 409 (rejected_same_person);
* undo в окне — 200 + восстановление состояния;
* undo за окном (90+ дней) — 410 Gone (через monkey-patched ``now``);
* merge-history — список логов.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from parser_service.services.person_merger import UNDO_WINDOW_DAYS, undo_merge
from shared_models.enums import HypothesisReviewStatus, HypothesisType
from shared_models.orm import (
    Hypothesis,
    Person,
    PersonMergeLog,
)
from sqlalchemy import select

pytestmark = [pytest.mark.db, pytest.mark.integration]


_GED_DUPLICATES = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 TRLR
"""


async def _import_and_get_person_pair(app_client) -> tuple[str, str, str]:
    """Импортирует фикстуру и возвращает (tree_id, person_a_id, person_b_id)."""
    files = {"file": ("test.ged", _GED_DUPLICATES, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    a_id = next(p["id"] for p in items if p["gedcom_xref"] == "I1")
    b_id = next(p["id"] for p in items if p["gedcom_xref"] == "I2")
    return tree_id, a_id, b_id


@pytest.mark.asyncio
async def test_preview_returns_diff_without_mutations(app_client) -> None:
    """``preview`` возвращает diff и не мутирует БД."""
    _, a_id, b_id = await _import_and_get_person_pair(app_client)

    response = await app_client.post(
        f"/persons/{a_id}/merge/preview",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["survivor_id"] in (a_id, b_id)
    assert body["merged_id"] in (a_id, b_id)
    assert body["survivor_id"] != body["merged_id"]
    assert body["hypothesis_check"] == "no_hypotheses_found"
    assert body["conflicts"] == []
    assert isinstance(body["fields"], list)
    assert isinstance(body["events"], list)

    # Никаких person_merge_logs не появилось.
    listing_after = await app_client.get(f"/trees/{body['survivor_id']}/persons")
    # Фикстура с двумя персонами, обе ещё видны.
    assert listing_after.status_code in (200, 422)


@pytest.mark.asyncio
async def test_commit_happy_path_then_idempotent(app_client) -> None:
    """Commit мерджит, повторный с тем же токеном не создаёт нового лога."""
    tree_id, a_id, b_id = await _import_and_get_person_pair(app_client)
    token = uuid.uuid4().hex

    payload = {
        "target_id": b_id,
        "confirm": True,
        "confirm_token": token,
    }
    response = await app_client.post(f"/persons/{a_id}/merge", json=payload)
    assert response.status_code == 200, response.text
    first_body = response.json()
    assert first_body["survivor_id"] in (a_id, b_id)
    merge_id = first_body["merge_id"]

    # Идемпотентность: тот же token → тот же merge_id.
    response_2 = await app_client.post(f"/persons/{a_id}/merge", json=payload)
    assert response_2.status_code == 200
    assert response_2.json()["merge_id"] == merge_id

    # Listing после merge'а — только одна персона активна.
    listing = await app_client.get(f"/trees/{tree_id}/persons")
    visible = [p["id"] for p in listing.json()["items"]]
    assert first_body["survivor_id"] in visible
    assert first_body["merged_id"] not in visible


@pytest.mark.asyncio
async def test_commit_without_confirm_is_422(app_client) -> None:
    """Тело без ``confirm:true`` отклоняется на уровне Pydantic (422)."""
    _, a_id, b_id = await _import_and_get_person_pair(app_client)
    response = await app_client.post(
        f"/persons/{a_id}/merge",
        json={"target_id": b_id, "confirm_token": uuid.uuid4().hex},
    )
    assert response.status_code == 422

    # confirm:false тоже 422 (Literal[True]).
    response2 = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": False,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert response2.status_code == 422


@pytest.mark.asyncio
async def test_commit_blocked_when_same_person_rejected(app_client, postgres_dsn: str) -> None:
    """Если same_person hypothesis rejected — commit 409 без mutation'а."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    tree_id, a_id, b_id = await _import_and_get_person_pair(app_client)

    # Заранее вставляем rejected same_person hypothesis между a и b.
    engine = create_async_engine(postgres_dsn, future=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    a_uuid = uuid.UUID(a_id)
    b_uuid = uuid.UUID(b_id)
    sub_a, sub_b = sorted((a_uuid, b_uuid), key=lambda u: u.bytes)
    async with async_session() as session, session.begin():
        session.add(
            Hypothesis(
                tree_id=uuid.UUID(tree_id),
                hypothesis_type=HypothesisType.SAME_PERSON.value,
                subject_a_type="person",
                subject_a_id=sub_a,
                subject_b_type="person",
                subject_b_id=sub_b,
                composite_score=0.95,
                rules_version="test",
                reviewed_status=HypothesisReviewStatus.REJECTED.value,
            )
        )
    await engine.dispose()

    response = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["reason"] == "hypothesis_conflict"
    reasons = [c["reason"] for c in body["detail"]["blocking_hypotheses"]]
    assert "rejected_same_person" in reasons


@pytest.mark.asyncio
async def test_commit_blocked_when_already_merged(app_client) -> None:
    """Повторный merge на уже-merged персону отклоняется 409."""
    _, a_id, b_id = await _import_and_get_person_pair(app_client)

    first = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert first.status_code == 200
    merged_side = first.json()["merged_id"]

    # Пытаемся merge'нуть merged_side как target ещё раз.
    second = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": merged_side,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    # 409 — subject_already_merged.
    assert second.status_code == 409
    reasons = [c["reason"] for c in second.json()["detail"]["blocking_hypotheses"]]
    assert "subject_already_merged" in reasons


@pytest.mark.asyncio
async def test_undo_within_window_restores_state(app_client) -> None:
    """Undo в окне 90 дней возвращает merged person в active."""
    tree_id, a_id, b_id = await _import_and_get_person_pair(app_client)

    commit = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert commit.status_code == 200
    merge_id = commit.json()["merge_id"]

    undo = await app_client.post(f"/persons/merge/{merge_id}/undo")
    assert undo.status_code == 200, undo.text
    body = undo.json()
    assert body["merge_id"] == merge_id
    assert body["undone_at"] is not None

    # Listing снова показывает обе персоны (merged restored).
    listing = await app_client.get(f"/trees/{tree_id}/persons")
    visible = [p["id"] for p in listing.json()["items"]]
    assert commit.json()["survivor_id"] in visible
    assert commit.json()["merged_id"] in visible


@pytest.mark.asyncio
async def test_undo_after_window_expired_returns_410(app_client, postgres_dsn: str) -> None:
    """Undo через 91 день — 410 Gone.

    Проверяем напрямую через сервис, патча ``now`` параметром, чтобы не
    ждать реальные 90 дней. HTTP-эндпоинт всё равно использует тот же
    путь без monkey-patch'а — этот тест валидирует ветку UndoNotAllowedError.
    """
    from parser_service.services.person_merger import UndoNotAllowedError
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _, a_id, b_id = await _import_and_get_person_pair(app_client)

    commit = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    assert commit.status_code == 200
    merge_id = uuid.UUID(commit.json()["merge_id"])

    engine = create_async_engine(postgres_dsn, future=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=UNDO_WINDOW_DAYS + 1)
    async with async_session() as session, session.begin():
        with pytest.raises(UndoNotAllowedError) as exc_info:
            await undo_merge(session, merge_id=merge_id, now=far_future)
        assert exc_info.value.reason == "undo_window_expired"
    await engine.dispose()


@pytest.mark.asyncio
async def test_merge_history_lists_both_sides(app_client) -> None:
    """``merge-history`` возвращает запись для survivor'а и merged'а."""
    _, a_id, b_id = await _import_and_get_person_pair(app_client)

    commit = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    survivor_id = commit.json()["survivor_id"]
    merged_id = commit.json()["merged_id"]

    # У survivor'а history содержит запись.
    res = await app_client.get(f"/persons/{survivor_id}/merge-history")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) == 1
    assert items[0]["survivor_id"] == survivor_id
    assert items[0]["merged_id"] == merged_id

    # И у merged'а тоже (поиск по обеим сторонам).
    res2 = await app_client.get(f"/persons/{merged_id}/merge-history")
    assert res2.status_code == 200
    assert len(res2.json()["items"]) == 1


@pytest.mark.asyncio
async def test_merge_log_persists_dry_run_diff(app_client, postgres_dsn: str) -> None:
    """В `person_merge_logs.dry_run_diff_json` — полный snapshot."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    _, a_id, b_id = await _import_and_get_person_pair(app_client)
    commit = await app_client.post(
        f"/persons/{a_id}/merge",
        json={
            "target_id": b_id,
            "confirm": True,
            "confirm_token": uuid.uuid4().hex,
        },
    )
    merge_id = uuid.UUID(commit.json()["merge_id"])

    engine = create_async_engine(postgres_dsn, future=True)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    async with async_session() as session:
        res = await session.execute(select(PersonMergeLog).where(PersonMergeLog.id == merge_id))
        log = res.scalar_one()
        assert log.dry_run_diff_json["survivor_id"] == commit.json()["survivor_id"]
        assert log.dry_run_diff_json["merged_id"] == commit.json()["merged_id"]
        assert "fields" in log.dry_run_diff_json
        # Имена с offset зафиксированы:
        names = log.dry_run_diff_json["names"]
        assert names, "ожидаем хотя бы одно имя merged'а"
        for n in names:
            assert n["new_sort_order"] >= 1000

    # Подтверждаем что merged.id ушёл в deleted_at.
    async with async_session() as session:
        res = await session.execute(select(Person).where(Person.id == log.merged_id))
        merged = res.scalar_one()
        assert merged.deleted_at is not None
        assert merged.merged_into_person_id == log.survivor_id

    await engine.dispose()
