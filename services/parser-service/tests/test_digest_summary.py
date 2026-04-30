"""Интеграционные тесты ``GET /users/{id}/digest-summary`` (Phase 14.2).

Проверяем:

* 503 без сконфигурированного ``internal_service_token``;
* 401 при отсутствующем / неправильном header'е;
* 200 + правильные счётчики на свежеимпортированном дереве;
* фильтр по ``since`` (старые persons вне окна);
* top-3 cards по created_at DESC с primary_name;
* hypotheses pending count.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest
from shared_models.enums import HypothesisReviewStatus
from shared_models.orm import Hypothesis, Tree
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


_GED_FIXTURE = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Anna /Petrova/
1 SEX F
0 @I2@ INDI
1 NAME Boris /Petrov/
1 SEX M
0 @I3@ INDI
1 NAME Catherine /Sidorova/
1 SEX F
0 TRLR
"""

_TOKEN = "x" * 32


@pytest.fixture
def _configured_token(app):
    """Прокинуть internal_service_token в Settings через override."""
    from parser_service.config import Settings, get_settings

    s = Settings(internal_service_token=_TOKEN)
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides.pop(get_settings, None)


async def _import_fixture(app_client) -> tuple[uuid.UUID, uuid.UUID]:
    """Импортировать фикстуру и вернуть ``(tree_id, owner_user_id)``."""
    files = {"file": ("digest.ged", _GED_FIXTURE, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code in (200, 201), created.text
    tree_id = uuid.UUID(created.json()["tree_id"])

    sync_url = os.environ["DATABASE_URL"]
    async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        owner_id = await session.scalar(select(Tree.owner_user_id).where(Tree.id == tree_id))
        assert owner_id is not None
    await engine.dispose()
    return tree_id, owner_id


@pytest.mark.asyncio
async def test_digest_summary_503_without_configured_token(app_client, app) -> None:
    """Settings.internal_service_token пуст → 503."""
    from parser_service.config import Settings, get_settings

    app.dependency_overrides[get_settings] = lambda: Settings(internal_service_token="")
    try:
        user_id = uuid.uuid4()
        since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()
        response = await app_client.get(
            f"/users/{user_id}/digest-summary",
            params={"since": since},
            headers={"X-Internal-Service-Token": _TOKEN},
        )
        assert response.status_code == 503
        assert "Internal service token not configured" in response.text
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_401_without_header(app_client) -> None:
    user_id = uuid.uuid4()
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()
    response = await app_client.get(
        f"/users/{user_id}/digest-summary",
        params={"since": since},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_401_wrong_token(app_client) -> None:
    user_id = uuid.uuid4()
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()
    response = await app_client.get(
        f"/users/{user_id}/digest-summary",
        params={"since": since},
        headers={"X-Internal-Service-Token": "y" * 32},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_counts_new_persons_in_window(app_client) -> None:
    """3 импортированных persons → new_persons_count=3, top_3 заполнен."""
    _, owner_id = await _import_fixture(app_client)
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()

    response = await app_client.get(
        f"/users/{owner_id}/digest-summary",
        params={"since": since},
        headers={"X-Internal-Service-Token": _TOKEN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_persons_count"] == 3
    assert body["new_hypotheses_pending"] == 0
    assert len(body["top_3_recent_persons"]) == 3
    names = {p["primary_name"] for p in body["top_3_recent_persons"]}
    assert names == {"Anna Petrova", "Boris Petrov", "Catherine Sidorova"}


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_since_filter_excludes_old(app_client) -> None:
    """``since`` в будущем → 0 новых persons (все импортированные старше)."""
    _, owner_id = await _import_fixture(app_client)
    future = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()

    response = await app_client.get(
        f"/users/{owner_id}/digest-summary",
        params={"since": future},
        headers={"X-Internal-Service-Token": _TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["new_persons_count"] == 0
    assert body["top_3_recent_persons"] == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_counts_pending_hypotheses(app_client) -> None:
    """Pending-hypothesis count берётся как snapshot, не привязан к ``since``."""
    tree_id, owner_id = await _import_fixture(app_client)

    sync_url = os.environ["DATABASE_URL"]
    async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(async_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        # Inject две pending-hypothesis на это дерево. UNIQUE constraint
        # требует уникальной (type, subject_a, subject_b) — берём разные
        # пары persons.
        for i in range(2):
            session.add(
                Hypothesis(
                    tree_id=tree_id,
                    hypothesis_type=f"duplicate_person_{i}",
                    subject_a_type="person",
                    subject_a_id=uuid.uuid4(),
                    subject_b_type="person",
                    subject_b_id=uuid.uuid4(),
                    composite_score=0.5,
                    rules_version="test-1",
                    reviewed_status=HypothesisReviewStatus.PENDING.value,
                )
            )
        await session.commit()
    await engine.dispose()

    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()
    response = await app_client.get(
        f"/users/{owner_id}/digest-summary",
        params={"since": since},
        headers={"X-Internal-Service-Token": _TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["new_hypotheses_pending"] == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("_configured_token")
async def test_digest_summary_unknown_user_returns_zero(app_client) -> None:
    """User без owned-trees: счётчики = 0, top пуст."""
    user_id = uuid.uuid4()
    since = (dt.datetime.now(dt.UTC) - dt.timedelta(days=7)).isoformat()

    response = await app_client.get(
        f"/users/{user_id}/digest-summary",
        params={"since": since},
        headers={"X-Internal-Service-Token": _TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["new_persons_count"] == 0
    assert body["new_hypotheses_pending"] == 0
    assert body["top_3_recent_persons"] == []
