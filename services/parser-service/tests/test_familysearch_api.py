"""Интеграционные тесты ``POST /imports/familysearch``.

FS-API мокается через monkeypatch на :class:`FamilySearchClient`.
``access_token`` в тестах — фиктивный, реальные сетевые вызовы не
происходят.

Маркеры: ``db`` + ``integration`` — требуют testcontainers Postgres.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from familysearch_client import (
    AuthError,
    FsFact,
    FsName,
    FsPedigreeNode,
    FsPerson,
    NotFoundError,
    RateLimitError,
)
from shared_models.orm import Tree, User
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _solo_pedigree(fs_id: str = "KW7S-VQJ") -> FsPedigreeNode:
    return FsPedigreeNode(
        person=FsPerson(
            id=fs_id,
            names=(
                FsName(full_text="Solo Person", given="Solo", surname="Person", preferred=True),
            ),
            facts=(
                FsFact(
                    type="Birth",
                    date_original="1900",
                    place_original="Brooklyn, New York",
                ),
            ),
        )
    )


@pytest_asyncio.fixture
async def fresh_tree_id(postgres_dsn: str) -> uuid.UUID:
    """Создаёт user + пустое tree через прямую сессию (минуя FastAPI app).

    Возвращает только tree_id; owner_user_id — связан в БД.
    """
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(
            email=f"fs-api-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:fs-api-{uuid.uuid4().hex[:8]}",
            display_name="FS API Test User",
            locale="en",
        )
        session.add(user)
        await session.flush()
        tree = Tree(
            owner_user_id=user.id,
            name=f"FS API Test {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.commit()
        result = tree.id
    await engine.dispose()
    return result


def _patch_fs_client_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pedigree: FsPedigreeNode | None = None,
    raises: Exception | None = None,
) -> None:
    """Заменяет FamilySearchClient в importer на stub.

    importer создаёт собственный FamilySearchClient(access_token=...) внутри
    ``async with``-блока. Patch'им сам класс на stub-фабрику, которая
    возвращает объект с async-context-manager + get_pedigree.
    """

    class _StubFsClientCM:
        def __init__(self, **kwargs: Any) -> None:
            # Принимаем любые kwargs (access_token, config, ...) — stub.
            self._kwargs = kwargs

        async def __aenter__(self) -> _StubFsClientCM:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get_pedigree(
            self,
            person_id: str,  # noqa: ARG002
            *,
            generations: int = 4,  # noqa: ARG002
        ) -> FsPedigreeNode:
            if raises is not None:
                raise raises
            assert pedigree is not None
            return pedigree

    monkeypatch.setattr(
        "parser_service.services.familysearch_importer.FamilySearchClient",
        _StubFsClientCM,
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_fs_person_id_returns_422(app_client) -> None:  # type: ignore[no-untyped-def]
    """Lowercase / spaces / spec-symbols в fs_person_id → 422 от Pydantic."""
    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 20,
            "fs_person_id": "lowercase-bad",  # !valid: pattern requires A-Z 0-9 -
            "tree_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_short_access_token_returns_422(app_client) -> None:  # type: ignore[no-untyped-def]
    """access_token короче 10 chars → 422."""
    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "short",
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_generations_out_of_range_returns_422(app_client) -> None:  # type: ignore[no-untyped-def]
    """generations > 8 → 422 (FS personal apps cap)."""
    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 20,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(uuid.uuid4()),
            "generations": 9,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_unknown_tree_returns_404(app_client) -> None:  # type: ignore[no-untyped-def]
    """Несуществующий tree_id → 404."""
    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 20,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(uuid.uuid4()),
            "generations": 1,
        },
    )
    assert response.status_code == 404, response.text
    assert "tree" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_familysearch_import_success(
    app_client,  # type: ignore[no-untyped-def]
    fresh_tree_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Полный happy path: 201 + ImportJobResponse с status=succeeded."""
    _patch_fs_client_factory(monkeypatch, pedigree=_solo_pedigree())

    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 30,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(fresh_tree_id),
            "generations": 1,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["tree_id"] == str(fresh_tree_id)
    assert body["stats"]["persons"] == 1
    assert body["stats"]["events"] == 1
    assert body["stats"]["generations"] == 1


@pytest.mark.asyncio
async def test_get_familysearch_import_returns_job(
    app_client,  # type: ignore[no-untyped-def]
    fresh_tree_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /imports/familysearch/{id} возвращает тот же ImportJob."""
    _patch_fs_client_factory(monkeypatch, pedigree=_solo_pedigree())
    created = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 30,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(fresh_tree_id),
            "generations": 1,
        },
    )
    job_id = created.json()["id"]

    fetched = await app_client.get(f"/imports/familysearch/{job_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["id"] == job_id
    assert fetched.json()["stats"]["persons"] == 1


# ---------------------------------------------------------------------------
# Error mapping (FS upstream errors → HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fs_404_returns_404(
    app_client,  # type: ignore[no-untyped-def]
    fresh_tree_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FS NotFoundError → 404 с упоминанием fs_person_id в detail."""
    _patch_fs_client_factory(monkeypatch, raises=NotFoundError("404"))

    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 30,
            "fs_person_id": "GHOST-XX",
            "tree_id": str(fresh_tree_id),
            "generations": 1,
        },
    )
    assert response.status_code == 404
    assert "GHOST-XX" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fs_401_returns_401_to_user(
    app_client,  # type: ignore[no-untyped-def]
    fresh_tree_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Битый access_token → AuthError → 401 (frontend инициирует re-auth)."""
    _patch_fs_client_factory(monkeypatch, raises=AuthError("invalid_token"))

    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 30,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(fresh_tree_id),
            "generations": 1,
        },
    )
    assert response.status_code == 401
    assert "FamilySearch" in response.json()["detail"]


@pytest.mark.asyncio
async def test_fs_429_returns_429_with_retry_after(
    app_client,  # type: ignore[no-untyped-def]
    fresh_tree_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RateLimitError → 429 + Retry-After header."""
    _patch_fs_client_factory(monkeypatch, raises=RateLimitError("rate limited", retry_after=12.0))

    response = await app_client.post(
        "/imports/familysearch",
        json={
            "access_token": "x" * 30,
            "fs_person_id": "KW7S-VQJ",
            "tree_id": str(fresh_tree_id),
            "generations": 1,
        },
    )
    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "12"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_token_fingerprint_is_first_8_chars_of_sha256() -> None:
    """sha256(access_token)[:8] — детерминированный, никаких leaks."""
    from parser_service.api.familysearch import _token_fingerprint

    fingerprint = _token_fingerprint("super-secret-token-123")
    assert len(fingerprint) == 8
    # Ensure the fingerprint is hex.
    int(fingerprint, 16)
    # И что сам токен в нём не присутствует.
    assert "super-secret" not in fingerprint
