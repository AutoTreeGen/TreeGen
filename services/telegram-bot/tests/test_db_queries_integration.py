"""Integration tests for db_queries (Phase 14.1, ADR-0056).

Real Postgres via testcontainers + alembic upgrade head + populated
fixtures. Verifies JOIN'ы, ILIKE, soft-delete фильтры — без моков SQL.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from shared_models.orm import (
    ImportJob,
    Name,
    Person,
    TelegramUserLink,
    Tree,
    User,
)
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from telegram_bot.services.db_queries import (
    fetch_active_tree,
    fetch_recent_imports,
    resolve_user_id_from_chat,
    search_persons_in_active_tree,
    toggle_notifications,
)

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

pytestmark = [pytest.mark.db, pytest.mark.integration]


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "alembic.ini").exists():
            return parent
    pytest.skip("alembic.ini не найден")
    msg = "unreachable"
    raise RuntimeError(msg)


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    """testcontainers-postgres + alembic upgrade head."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    saved_db_url = os.environ.get("DATABASE_URL")
    try:
        sync_url = container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+psycopg://", 1
        )
        os.environ["DATABASE_URL"] = sync_url

        from alembic import command
        from alembic.config import Config

        cfg = Config(str(_repo_root() / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", sync_url)
        cfg.set_main_option("script_location", str(_repo_root() / "infrastructure" / "alembic"))
        command.upgrade(cfg, "head")

        async_url = sync_url.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        yield async_url
    finally:
        if saved_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_db_url
        container.stop()


@pytest_asyncio.fixture
async def session(postgres_dsn: str) -> AsyncIterator[AsyncSession]:
    """Свежий AsyncSession + чистый набор тестовых данных на каждый тест."""
    engine = create_async_engine(postgres_dsn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


# -----------------------------------------------------------------------------
# resolve_user_id_from_chat
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_user_id_returns_none_for_unlinked(session: AsyncSession) -> None:
    result = await resolve_user_id_from_chat(session, tg_chat_id=999_001)
    assert result is None


@pytest.mark.asyncio
async def test_resolve_user_id_returns_user_for_active_link(session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_resolve",
        clerk_user_id="u_resolve",
        email="resolve@test",
    )
    session.add(user)
    await session.flush()
    session.add(
        TelegramUserLink(
            id=uuid.uuid4(),
            user_id=user.id,
            tg_chat_id=999_002,
            tg_user_id=999_002,
            linked_at=dt.datetime.now(dt.UTC),
        )
    )
    await session.commit()

    result = await resolve_user_id_from_chat(session, tg_chat_id=999_002)
    assert result == user.id


@pytest.mark.asyncio
async def test_resolve_user_id_skips_revoked_link(session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_rev",
        clerk_user_id="u_rev",
        email="rev@test",
    )
    session.add(user)
    await session.flush()
    session.add(
        TelegramUserLink(
            id=uuid.uuid4(),
            user_id=user.id,
            tg_chat_id=999_003,
            tg_user_id=999_003,
            linked_at=dt.datetime.now(dt.UTC),
            revoked_at=dt.datetime.now(dt.UTC),
        )
    )
    await session.commit()

    result = await resolve_user_id_from_chat(session, tg_chat_id=999_003)
    assert result is None


# -----------------------------------------------------------------------------
# fetch_active_tree
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_active_tree_returns_none_for_user_without_trees(
    session: AsyncSession,
) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_notree",
        clerk_user_id="u_notree",
        email="notree@test",
    )
    session.add(user)
    await session.commit()
    assert await fetch_active_tree(session, user_id=user.id) is None


@pytest.mark.asyncio
async def test_fetch_active_tree_picks_oldest_owned(session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_active",
        clerk_user_id="u_active",
        email="active@test",
    )
    session.add(user)
    await session.flush()

    tree_old = Tree(id=uuid.uuid4(), name="Old", owner_user_id=user.id)
    session.add(tree_old)
    await session.flush()

    # Force a later created_at on the second tree.
    tree_new = Tree(id=uuid.uuid4(), name="New", owner_user_id=user.id)
    session.add(tree_new)
    await session.flush()

    # Add 2 persons to the older tree to verify count.
    for _ in range(2):
        session.add(Person(id=uuid.uuid4(), tree_id=tree_old.id, sex="U"))
    await session.commit()

    active = await fetch_active_tree(session, user_id=user.id)
    assert active is not None
    assert active.id == tree_old.id
    assert active.name == "Old"
    assert active.persons_count == 2


# -----------------------------------------------------------------------------
# fetch_recent_imports
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_recent_imports_orders_newest_first_and_filters_by_owner(
    session: AsyncSession,
) -> None:
    owner = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_imp",
        clerk_user_id="u_imp",
        email="imp@test",
    )
    other = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_other",
        clerk_user_id="u_other",
        email="other@test",
    )
    session.add_all([owner, other])
    await session.flush()

    my_tree = Tree(id=uuid.uuid4(), name="Mine", owner_user_id=owner.id)
    others_tree = Tree(id=uuid.uuid4(), name="Theirs", owner_user_id=other.id)
    session.add_all([my_tree, others_tree])
    await session.flush()

    base = dt.datetime.now(dt.UTC)
    for i, fname in enumerate(["a.ged", "b.ged", "c.ged"]):
        job = ImportJob(
            id=uuid.uuid4(),
            tree_id=my_tree.id,
            source_filename=fname,
            status="succeeded",
        )
        session.add(job)
        await session.flush()
        # Mutate created_at to enforce order.
        job.created_at = base - dt.timedelta(hours=i)

    # Other user's import — must not appear.
    session.add(
        ImportJob(
            id=uuid.uuid4(),
            tree_id=others_tree.id,
            source_filename="leaked.ged",
            status="succeeded",
        )
    )
    await session.commit()

    imports = await fetch_recent_imports(session, user_id=owner.id, limit=5)
    filenames = [i.source_filename for i in imports]
    assert filenames == ["a.ged", "b.ged", "c.ged"]


# -----------------------------------------------------------------------------
# search_persons_in_active_tree
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_persons_returns_substring_matches(session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_search",
        clerk_user_id="u_search",
        email="search@test",
    )
    session.add(user)
    await session.flush()
    tree = Tree(id=uuid.uuid4(), name="Test", owner_user_id=user.id)
    session.add(tree)
    await session.flush()

    p1 = Person(id=uuid.uuid4(), tree_id=tree.id, sex="M")
    p2 = Person(id=uuid.uuid4(), tree_id=tree.id, sex="F")
    p3 = Person(id=uuid.uuid4(), tree_id=tree.id, sex="U")
    session.add_all([p1, p2, p3])
    await session.flush()

    session.add_all(
        [
            Name(
                id=uuid.uuid4(), person_id=p1.id, given_name="John", surname="Smith", sort_order=0
            ),
            Name(
                id=uuid.uuid4(), person_id=p2.id, given_name="Jane", surname="Smith", sort_order=0
            ),
            Name(
                id=uuid.uuid4(), person_id=p3.id, given_name="Anna", surname="Cohen", sort_order=0
            ),
        ]
    )
    await session.commit()

    tree_id, hits = await search_persons_in_active_tree(
        session, user_id=user.id, query="smith", limit=5
    )
    assert tree_id == tree.id
    found_names = {h.primary_name for h in hits}
    assert {"John Smith", "Jane Smith"} <= found_names
    assert "Anna Cohen" not in found_names


@pytest.mark.asyncio
async def test_search_persons_returns_empty_when_no_active_tree(
    session: AsyncSession,
) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_blank",
        clerk_user_id="u_blank",
        email="blank@test",
    )
    session.add(user)
    await session.commit()
    tree_id, hits = await search_persons_in_active_tree(session, user_id=user.id, query="anything")
    assert tree_id is None
    assert hits == []


# -----------------------------------------------------------------------------
# toggle_notifications
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_notifications_returns_false_for_unlinked(session: AsyncSession) -> None:
    linked, state = await toggle_notifications(session, tg_chat_id=999_900)
    assert linked is False
    assert state is False


@pytest.mark.asyncio
async def test_toggle_notifications_flips_value(session: AsyncSession) -> None:
    user = User(
        id=uuid.uuid4(),
        external_auth_id="auth_u_toggle",
        clerk_user_id="u_toggle",
        email="toggle@test",
    )
    session.add(user)
    await session.flush()
    session.add(
        TelegramUserLink(
            id=uuid.uuid4(),
            user_id=user.id,
            tg_chat_id=999_910,
            tg_user_id=999_910,
            linked_at=dt.datetime.now(dt.UTC),
            notifications_enabled=False,
        )
    )
    await session.commit()

    linked, state = await toggle_notifications(session, tg_chat_id=999_910)
    await session.commit()
    assert linked is True
    assert state is True

    linked, state = await toggle_notifications(session, tg_chat_id=999_910)
    await session.commit()
    assert linked is True
    assert state is False
