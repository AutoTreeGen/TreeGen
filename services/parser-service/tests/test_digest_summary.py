"""Интеграционные тесты ``GET /users/{id}/digest-summary`` (Phase 14.2).

Проверяем:

* 503 без сконфигурированного ``internal_service_token``;
* 401 при отсутствующем / неправильном header'е;
* 200 + правильные счётчики на свежеимпортированном дереве;
* фильтр по ``since`` (старые persons вне окна);
* top-3 cards по created_at DESC с primary_name;
* hypotheses pending count.

Изоляция: каждый «count»-тест создаёт **уникального** ``User`` и ``Tree``
напрямую через ORM (минуя ``/imports``), потому что parser-service test
session делит один Postgres-instance между всеми тестами, а
``conftest._fake_current_user_id_override`` отдаёт фиксированный
``clerk_user_id`` → один и тот же ``users.id`` на всю сессию. Любой
запрос к ``/digest-summary?user_id=<fake>`` иначе видит сотни персон,
которые загрузили другие тесты в эту же fake-user'у.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest
from shared_models.enums import HypothesisReviewStatus
from shared_models.orm import Hypothesis, Name, Person, Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


_TOKEN = "x" * 32


@pytest.fixture
def _configured_token(app):
    """Прокинуть internal_service_token в Settings через override."""
    from parser_service.config import Settings, get_settings

    s = Settings(internal_service_token=_TOKEN)
    app.dependency_overrides[get_settings] = lambda: s
    yield s
    app.dependency_overrides.pop(get_settings, None)


def _async_engine():
    """Engine для прямых ORM-операций (минуя FastAPI app_client)."""
    sync_url = os.environ["DATABASE_URL"]
    async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    return create_async_engine(async_url)


async def _create_isolated_user_and_tree(
    *,
    persons: list[tuple[str, str]] | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Создать уникальные User+Tree+Persons через прямой ORM-insert.

    Возвращает ``(tree_id, user_id)``. ``persons`` — список
    ``(given_name, surname)`` для добавления; ``None`` = без persons.
    """
    engine = _async_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            unique = uuid.uuid4().hex[:12]
            user = User(
                email=f"digest-{unique}@test.local",
                external_auth_id=f"local:digest-{unique}",
                clerk_user_id=None,
                display_name="Digest Test User",
                locale="en",
            )
            session.add(user)
            await session.flush()

            tree = Tree(
                owner_user_id=user.id,
                name=f"digest-test-tree-{unique}",
            )
            session.add(tree)
            await session.flush()

            if persons:
                for given, surname in persons:
                    person = Person(tree_id=tree.id, sex="U")
                    session.add(person)
                    await session.flush()
                    session.add(
                        Name(
                            person_id=person.id,
                            given_name=given,
                            surname=surname,
                            sort_order=0,
                        )
                    )
            await session.commit()
            return tree.id, user.id
    finally:
        await engine.dispose()


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
    """3 свежесозданных persons → new_persons_count=3, top_3 заполнен."""
    _, owner_id = await _create_isolated_user_and_tree(
        persons=[
            ("Anna", "Petrova"),
            ("Boris", "Petrov"),
            ("Catherine", "Sidorova"),
        ]
    )
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
    _, owner_id = await _create_isolated_user_and_tree(persons=[("Anna", "Petrova")])
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
    tree_id, owner_id = await _create_isolated_user_and_tree()

    engine = _async_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            # UNIQUE constraint требует разных (type, subject_a, subject_b).
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
    finally:
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
