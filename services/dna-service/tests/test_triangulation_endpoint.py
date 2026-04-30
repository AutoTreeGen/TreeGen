"""Phase 6.4 — integration тесты `GET /trees/{tree_id}/triangulation`.

Покрытие:
    - 403 Forbidden для пользователя без membership/owner на дереве.
    - 200 OK для VIEWER+ (использует tree.owner_user_id fallback из
      ``permissions.get_user_role_in_tree``).
    - cache hit: второй вызов не делает БД-запросов (proven via session-spy).
    - формат ответа (JSON-схема, sort, пустой ответ для tree без matches).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from shared_models.orm import DnaKit, DnaMatch, SharedMatch, Tree, User
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def _seed_tree_with_triangulating_matches(
    postgres_dsn: str,
) -> dict[str, Any]:
    """Создаёт User + Tree + DnaKit + 3 DnaMatch + SharedMatch'и.

    Layout:
        - 3 matches A/B/C, все попарно SharedMatch.
        - Все 3 имеют один сегмент chr=1, [10, 30] cM (overlap > 7 cM).
        - Один extra match D на той же хромосоме, но БЕЗ shared-relations
          с A/B/C (не должен попасть в группу).

    Возвращает: {user_id, tree_id, kit_id, match_ids: dict[str, UUID]}.
    """
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"triangulation-test-{suffix}@example.com",
            external_auth_id=f"auth0|triangulation-test-{suffix}",
            display_name="Triangulation Test User",
            clerk_user_id=f"user_triangulation_{suffix}",
        )
        session.add(user)
        await session.flush()

        tree = Tree(owner_user_id=user.id, name=f"Triangulation Tree {suffix}")
        session.add(tree)
        await session.flush()

        kit = DnaKit(
            tree_id=tree.id,
            owner_user_id=user.id,
            source_platform="ancestry",
            external_kit_id=f"kit-{suffix}",
            display_name=f"Test Kit {suffix}",
        )
        session.add(kit)
        await session.flush()

        # 4 matches; провенанс с cM-сегментами для триангуляции.
        match_specs = [
            ("a", 10.0, 30.0),
            ("b", 12.0, 28.0),
            ("c", 8.0, 24.0),
            ("d", 12.0, 28.0),  # Тот же сегмент, но без SharedMatch-связей.
        ]
        match_ids: dict[str, uuid.UUID] = {}
        for label, start_cm, end_cm in match_specs:
            match = DnaMatch(
                tree_id=tree.id,
                kit_id=kit.id,
                external_match_id=f"ext-{label}-{suffix}",
                display_name=f"Match {label.upper()}",
                total_cm=20.0,
                provenance={
                    "segments": [
                        {
                            "chromosome": 1,
                            "start_cm": start_cm,
                            "end_cm": end_cm,
                        },
                    ],
                },
            )
            session.add(match)
            await session.flush()
            match_ids[label] = match.id

        # Mutual SharedMatch'и среди A/B/C (нормализация match_a_id < match_b_id).
        triple_ids = [match_ids["a"], match_ids["b"], match_ids["c"]]
        for i in range(len(triple_ids)):
            for j in range(i + 1, len(triple_ids)):
                a_id, b_id = sorted([triple_ids[i], triple_ids[j]])
                session.add(
                    SharedMatch(
                        tree_id=tree.id,
                        kit_id=kit.id,
                        match_a_id=a_id,
                        match_b_id=b_id,
                    )
                )

        await session.flush()
        result = {
            "user_id": user.id,
            "tree_id": tree.id,
            "kit_id": kit.id,
            "match_ids": match_ids,
        }
    await engine.dispose()
    return result


async def _seed_lone_user_and_tree(postgres_dsn: str) -> dict[str, uuid.UUID]:
    """Создаёт user'а и tree, но user НЕ owner/member дерева — для 403."""
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        owner = User(
            email=f"owner-{suffix}@example.com",
            external_auth_id=f"auth0|owner-{suffix}",
        )
        outsider = User(
            email=f"outsider-{suffix}@example.com",
            external_auth_id=f"auth0|outsider-{suffix}",
        )
        session.add_all([owner, outsider])
        await session.flush()
        tree = Tree(owner_user_id=owner.id, name=f"Private Tree {suffix}")
        session.add(tree)
        await session.flush()
        result = {"outsider_user_id": outsider.id, "tree_id": tree.id}
    await engine.dispose()
    return result


class _InMemoryCache:
    """Thread-safe (single-loop) in-memory cache для тестов.

    Совместима с :class:`dna_service.services.cache.CacheBackend` Protocol:
    реализует ``get`` + ``setex``. ``setex`` игнорирует TTL (тестам не нужен
    expiry), но мы засчитываем call'ы для проверки cache-write happened.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: int = 0
        self.get_calls: int = 0

    async def get(self, key: str) -> str | None:
        self.get_calls += 1
        return self._store.get(key)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        del ttl_seconds  # игнорируем — TTL тестируется в отдельном слое
        self.set_calls += 1
        self._store[key] = value


@pytest_asyncio.fixture
async def app_client_with_cache(
    postgres_dsn: str,
    storage_root,
) -> AsyncIterator[tuple[Any, _InMemoryCache, Any]]:
    """app-client + in-memory cache + helper для override RequireUser.

    Возвращает кортеж ``(client, cache, set_user)`` где ``set_user(user_id)``
    подменяет ``get_current_user_id`` для дальнейших запросов.
    """
    import os

    os.environ["DNA_SERVICE_DATABASE_URL"] = postgres_dsn
    os.environ["DNA_SERVICE_STORAGE_ROOT"] = str(storage_root)
    os.environ["DNA_SERVICE_REQUIRE_ENCRYPTION"] = "false"

    from dna_service.auth import (
        get_clerk_settings,
        get_current_claims,
        get_current_user_id,
    )
    from dna_service.database import dispose_engine, init_engine
    from dna_service.main import app
    from dna_service.services.cache import get_cache
    from httpx import ASGITransport, AsyncClient
    from shared_models.auth import ClerkClaims, ClerkJwtSettings

    fake_claims = ClerkClaims(
        sub="user_test_triangulation_clerk_sub",
        email="triangulation-test@autotreegen.test",
        raw={"sub": "user_test_triangulation_clerk_sub"},
    )

    async def _fake_current_claims() -> ClerkClaims:
        return fake_claims

    def _fake_clerk_settings() -> ClerkJwtSettings:
        return ClerkJwtSettings(issuer="https://test.clerk.local")

    cache = _InMemoryCache()

    def _fake_get_cache() -> _InMemoryCache:
        return cache

    # `_active_user_id[0]` — изменяется через ``set_user`` фабрику.
    _active_user_id: list[uuid.UUID | None] = [None]

    async def _fake_user_id() -> uuid.UUID:
        if _active_user_id[0] is None:
            msg = "Test forgot to call set_user(uuid) before issuing requests"
            raise RuntimeError(msg)
        return _active_user_id[0]

    def set_user(user_id: uuid.UUID) -> None:
        _active_user_id[0] = user_id

    app.dependency_overrides[get_clerk_settings] = _fake_clerk_settings
    app.dependency_overrides[get_current_claims] = _fake_current_claims
    app.dependency_overrides[get_current_user_id] = _fake_user_id
    app.dependency_overrides[get_cache] = _fake_get_cache

    init_engine(postgres_dsn)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, cache, set_user
    app.dependency_overrides.pop(get_clerk_settings, None)
    app.dependency_overrides.pop(get_current_claims, None)
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_cache, None)
    await dispose_engine()


# ---- 403 / 200 permission tests --------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_returns_403_for_user_without_tree_membership(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """User не-owner и без membership на дереве → 403 Forbidden."""
    client, _, set_user = app_client_with_cache
    seed = await _seed_lone_user_and_tree(postgres_dsn)
    set_user(seed["outsider_user_id"])

    resp = await client.get(f"/trees/{seed['tree_id']}/triangulation")

    assert resp.status_code == 403, resp.text
    body = resp.json()
    assert "viewer" in body["detail"].lower() or "access" in body["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_returns_404_for_unknown_tree(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Несуществующее tree_id → 404 (privacy: не палим owner-tree IDs)."""
    client, _, set_user = app_client_with_cache
    # Любой существующий user — 404 пробивается до permission-check'а.
    seed = await _seed_lone_user_and_tree(postgres_dsn)
    set_user(seed["outsider_user_id"])

    resp = await client.get(f"/trees/{uuid.uuid4()}/triangulation")
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_returns_200_with_groups_for_tree_owner(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Owner запрашивает свою триангуляцию → 200 + JSON с группой A/B/C."""
    client, _, set_user = app_client_with_cache
    seed = await _seed_tree_with_triangulating_matches(postgres_dsn)
    set_user(seed["user_id"])

    resp = await client.get(f"/trees/{seed['tree_id']}/triangulation")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tree_id"] == str(seed["tree_id"])
    assert body["min_overlap_cm"] == 7.0
    assert len(body["groups"]) == 1

    group = body["groups"][0]
    assert group["chromosome"] == 1
    assert group["start_cm"] == 12.0  # max(10, 12, 8)
    assert group["end_cm"] == 24.0  # min(30, 28, 24)
    expected_members = {
        str(seed["match_ids"]["a"]),
        str(seed["match_ids"]["b"]),
        str(seed["match_ids"]["c"]),
    }
    assert set(group["members"]) == expected_members
    # Match D в дереве, но нет SharedMatch'ей с A/B/C — не должен попасть.
    assert str(seed["match_ids"]["d"]) not in group["members"]


@pytest.mark.db
@pytest.mark.integration
async def test_min_overlap_cm_query_param_filters_groups(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """``min_overlap_cm=17.0`` отрезает все pairwise overlap'ы (макс. 16 cM
    у пары A-B) → пустой groups list."""
    client, _, set_user = app_client_with_cache
    seed = await _seed_tree_with_triangulating_matches(postgres_dsn)
    set_user(seed["user_id"])

    # Pairwise overlaps: A∩B=16cM, A∩C=14cM, B∩C=12cM. Threshold 17cM
    # отбрасывает все три.
    resp = await client.get(
        f"/trees/{seed['tree_id']}/triangulation",
        params={"min_overlap_cm": 17.0},
    )

    assert resp.status_code == 200
    assert resp.json()["groups"] == []
    # Sanity: тот же tree с порогом 7.0 даёт непустой результат.
    resp2 = await client.get(
        f"/trees/{seed['tree_id']}/triangulation",
        params={"min_overlap_cm": 7.0},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["groups"]) == 1


@pytest.mark.db
@pytest.mark.integration
async def test_empty_tree_returns_empty_groups_list(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Tree без matches → 200 с пустым groups list (не 404)."""
    client, _, set_user = app_client_with_cache
    seed = await _seed_lone_user_and_tree(postgres_dsn)
    # Делаем outsider-а владельцем second tree, чтобы прошёл permission gate.
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        new_tree = Tree(owner_user_id=seed["outsider_user_id"], name="Empty Tree")
        session.add(new_tree)
        await session.flush()
        empty_tree_id = new_tree.id
    await engine.dispose()
    set_user(seed["outsider_user_id"])

    resp = await client.get(f"/trees/{empty_tree_id}/triangulation")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["groups"] == []


# ---- caching --------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_second_call_hits_cache_skips_db(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Второй вызов с теми же параметрами читает из cache, не из БД.

    Считаем количество SQL-запросов на DnaMatch через SQLAlchemy event-hook;
    при cache hit это число не должно вырасти после первого вызова.
    """
    client, cache, set_user = app_client_with_cache
    seed = await _seed_tree_with_triangulating_matches(postgres_dsn)
    set_user(seed["user_id"])

    # Inspect-engine для отдельного monitoring; основной engine берётся
    # из dna_service.database, а event-listeners на ``before_cursor_execute``
    # цепляются на конкретный engine. Подключаемся к тому же DSN, считаем
    # SELECT'ы по таблице dna_matches за время вызова.
    from dna_service.database import get_engine

    engine = get_engine().sync_engine
    select_dna_matches: list[int] = [0]

    @event.listens_for(engine, "before_cursor_execute")
    def _count(
        conn,  # noqa: ARG001
        cursor,  # noqa: ARG001
        statement: str,
        parameters,  # noqa: ARG001
        context,  # noqa: ARG001
        executemany: bool,  # noqa: ARG001
    ) -> None:
        if "dna_matches" in statement.lower():
            select_dna_matches[0] += 1

    try:
        # Первый вызов — cache miss, читает БД.
        first = await client.get(f"/trees/{seed['tree_id']}/triangulation")
        assert first.status_code == 200
        first_count = select_dna_matches[0]
        assert first_count > 0, "first call did not query dna_matches"
        assert cache.set_calls == 1

        # Второй вызов — cache hit, не должен трогать dna_matches.
        second = await client.get(f"/trees/{seed['tree_id']}/triangulation")
        assert second.status_code == 200
        assert select_dna_matches[0] == first_count, (
            "second call queried dna_matches despite cache hit"
        )
        # Same body content.
        assert first.json() == second.json()
        # cache.set_calls остался 1 (мы не пишем повторно).
        assert cache.set_calls == 1
    finally:
        event.remove(engine, "before_cursor_execute", _count)


@pytest.mark.db
@pytest.mark.integration
async def test_different_min_overlap_uses_separate_cache_key(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Запросы с разными ``min_overlap_cm`` создают разные cache-ключи."""
    client, cache, set_user = app_client_with_cache
    seed = await _seed_tree_with_triangulating_matches(postgres_dsn)
    set_user(seed["user_id"])

    r1 = await client.get(
        f"/trees/{seed['tree_id']}/triangulation",
        params={"min_overlap_cm": 7.0},
    )
    r2 = await client.get(
        f"/trees/{seed['tree_id']}/triangulation",
        params={"min_overlap_cm": 10.0},
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert cache.set_calls == 2  # Два разных ключа → два write'а.


# ---- ignored data shapes ---------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_segments_without_cm_coords_are_skipped(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Сегменты с bp-only provenance (legacy, без start_cm/end_cm) пропускаются.

    Phase 6.4 contract: только cM-сегменты участвуют в триангуляции.
    Endpoint не должен падать на legacy provenance без cM-полей.
    """
    client, _, set_user = app_client_with_cache
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    async with factory() as session, session.begin():
        user = User(
            email=f"legacy-{suffix}@example.com",
            external_auth_id=f"auth0|legacy-{suffix}",
        )
        session.add(user)
        await session.flush()
        tree = Tree(owner_user_id=user.id, name=f"Legacy Tree {suffix}")
        session.add(tree)
        await session.flush()
        kit = DnaKit(
            tree_id=tree.id,
            owner_user_id=user.id,
            source_platform="ancestry",
            external_kit_id=f"kit-{suffix}",
            display_name="Legacy Kit",
        )
        session.add(kit)
        await session.flush()
        # Два match'а с bp-only сегментами + взаимный SharedMatch.
        ma = DnaMatch(
            tree_id=tree.id,
            kit_id=kit.id,
            provenance={
                "segments": [
                    {"chromosome": 1, "start_bp": 1_000_000, "end_bp": 5_000_000, "cm": 12.0},
                ],
            },
        )
        mb = DnaMatch(
            tree_id=tree.id,
            kit_id=kit.id,
            provenance={
                "segments": [
                    {"chromosome": 1, "start_bp": 1_500_000, "end_bp": 5_500_000, "cm": 12.0},
                ],
            },
        )
        session.add_all([ma, mb])
        await session.flush()
        a_id, b_id = sorted([ma.id, mb.id])
        session.add(SharedMatch(tree_id=tree.id, kit_id=kit.id, match_a_id=a_id, match_b_id=b_id))
        legacy_user_id = user.id
        legacy_tree_id = tree.id
    await engine.dispose()

    set_user(legacy_user_id)
    resp = await client.get(f"/trees/{legacy_tree_id}/triangulation")
    assert resp.status_code == 200, resp.text
    assert resp.json()["groups"] == []


@pytest.mark.db
@pytest.mark.integration
async def test_soft_deleted_kit_excludes_its_matches(
    app_client_with_cache,
    postgres_dsn,
) -> None:
    """Если consent revoke'нут (kit.deleted_at), его matches не триангулируют."""
    client, _, set_user = app_client_with_cache
    seed = await _seed_tree_with_triangulating_matches(postgres_dsn)

    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        kit: DnaKit = await session.get(DnaKit, seed["kit_id"])  # type: ignore[assignment]
        assert kit is not None
        kit.deleted_at = dt.datetime.now(dt.UTC)
    await engine.dispose()

    set_user(seed["user_id"])
    resp = await client.get(f"/trees/{seed['tree_id']}/triangulation")

    assert resp.status_code == 200
    assert resp.json()["groups"] == []
