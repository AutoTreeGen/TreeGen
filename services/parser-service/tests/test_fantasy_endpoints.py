"""FastAPI endpoint tests для fantasy filter (Phase 5.10).

Repo + runner замокан in-memory; тесты гонят через httpx ASGI client.
Покрывают:

* POST /fantasy-scan — вызывает runner и возвращает summary
* GET /fantasy-flags — фильтр по severity / dismissed / rule_id
* POST /fantasy-flags/{id}/dismiss — ставит dismissed_at
* POST /fantasy-flags/{id}/undismiss — очищает dismissed_at
* 404 для unknown flag_id и cross-tree access

DB не нужна — заменяем ``execute_fantasy_scan`` на stub и
``select(FantasyFlagOrm)`` через ``app.dependency_overrides[get_session]``
на in-memory store.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def fake_state() -> dict[str, Any]:
    """In-memory holder для flags — подменяет SQL store."""
    tree_id = uuid.UUID("11111111-1111-4111-8111-111111111111")
    flag_active_id = uuid.UUID("22222222-2222-4222-8222-222222222222")
    flag_dismissed_id = uuid.UUID("33333333-3333-4333-8333-333333333333")

    flags: dict[uuid.UUID, dict[str, Any]] = {
        flag_active_id: {
            "id": flag_active_id,
            "tree_id": tree_id,
            "subject_person_id": uuid.uuid4(),
            "subject_relationship_id": None,
            "rule_id": "birth_after_death",
            "severity": "critical",
            "confidence": 0.95,
            "reason": "Person I1 born 1900 but died 1850",
            "evidence_json": {"birth_year": 1900, "death_year": 1850},
            "dismissed_at": None,
            "dismissed_by": None,
            "dismissed_reason": None,
            "created_at": dt.datetime(2026, 5, 3, 12, 0, tzinfo=dt.UTC),
            "updated_at": dt.datetime(2026, 5, 3, 12, 0, tzinfo=dt.UTC),
        },
        flag_dismissed_id: {
            "id": flag_dismissed_id,
            "tree_id": tree_id,
            "subject_person_id": None,
            "subject_relationship_id": None,
            "rule_id": "circular_descent",
            "severity": "critical",
            "confidence": 0.95,
            "reason": "Cycle of A→B→A",
            "evidence_json": {"cycle_xrefs": ["I_A", "I_B"]},
            "dismissed_at": dt.datetime(2026, 5, 3, 13, 0, tzinfo=dt.UTC),
            "dismissed_by": None,
            "dismissed_reason": "verified merge — accepted",
            "created_at": dt.datetime(2026, 5, 3, 12, 0, tzinfo=dt.UTC),
            "updated_at": dt.datetime(2026, 5, 3, 13, 0, tzinfo=dt.UTC),
        },
    }
    return {
        "tree_id": tree_id,
        "flags": flags,
        "flag_active_id": flag_active_id,
        "flag_dismissed_id": flag_dismissed_id,
    }


class _FakeFlag:
    """Quack-typed как ORM FantasyFlag — нужен для ``model_validate(from_attributes=True)``."""

    def __init__(self, data: dict[str, Any]) -> None:
        for k, v in data.items():
            setattr(self, k, v)


class _FakeScalars:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _FakeResult:
    def __init__(self, items: list[Any] | None = None, single: Any | None = None) -> None:
        self._items = items or []
        self._single = single

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._items)

    def scalar_one_or_none(self) -> Any:
        return self._single


class _StubSession:
    """Minimal AsyncSession-like объект для роутера."""

    def __init__(self, state: dict[str, Any]) -> None:
        self._state = state

    async def execute(self, stmt: Any) -> _FakeResult:
        # crude pattern-match для SELECT vs UPDATE.
        from sqlalchemy.sql.dml import Update
        from sqlalchemy.sql.selectable import Select

        if isinstance(stmt, Update):
            # Apply update to in-memory flag.
            # Парсим: stmt.table_id (always FantasyFlag), .where(id == ?)
            # values dict
            params = stmt.compile().params
            target_id = params.get("id_1")
            if target_id is not None:
                flag = self._state["flags"].get(target_id)
                if flag is not None:
                    for k, v in stmt._values.items():
                        flag[k.key] = v.value if hasattr(v, "value") else v
            return _FakeResult()

        if isinstance(stmt, Select):
            # Determine target row(s). Поддерживаем два пути: by-id (single),
            # by-tree filter (list).
            params = stmt.compile().params
            tree_id = params.get("tree_id_1")
            # ``id_1`` (без ``tree_`` префикса) → single-flag-by-id query.
            single_id = params.get("id_1")
            # Если есть id-where, отдаём single.
            if single_id is not None and isinstance(single_id, uuid.UUID):
                flag = self._state["flags"].get(single_id)
                if flag and flag["tree_id"] == tree_id:
                    return _FakeResult(single=_FakeFlag(flag))
                return _FakeResult(single=None)
            # Иначе list, фильтруем по tree_id + опциональным сужениям.
            items = [_FakeFlag(f) for f in self._state["flags"].values() if f["tree_id"] == tree_id]
            return _FakeResult(items=items)
        return _FakeResult()

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest_asyncio.fixture
async def fantasy_client(
    monkeypatch: pytest.MonkeyPatch,
    fake_state: dict[str, Any],
) -> AsyncIterator[AsyncClient]:
    """TestClient с stub-session + stub runner."""
    monkeypatch.setenv("RATE_LIMITING_ENABLED", "false")
    monkeypatch.setenv("PARSER_SERVICE_CLERK_ISSUER", "https://clerk.test")

    from parser_service.auth import get_current_claims
    from parser_service.database import get_session
    from parser_service.main import app
    from parser_service.services import fantasy_runner

    async def _stub_session_dep() -> AsyncIterator[_StubSession]:
        yield _StubSession(fake_state)

    # Stub runner: возвращаем фиксированный summary без касания DB.
    async def _stub_run(
        _session: Any,
        tree_id: uuid.UUID,
        *,
        enabled_rules: frozenset[str] | None = None,  # noqa: ARG001
    ) -> fantasy_runner.ScanSummary:
        return fantasy_runner.ScanSummary(
            scan_id=uuid.UUID("44444444-4444-4444-8444-444444444444"),
            tree_id=tree_id,
            persons_scanned=10,
            families_scanned=3,
            flags_created=5,
            flags_replaced=2,
            by_severity={"critical": 2, "high": 1, "warning": 2},
        )

    monkeypatch.setattr(
        "parser_service.api.fantasy.execute_fantasy_scan",
        _stub_run,
    )

    app.dependency_overrides[get_session] = _stub_session_dep
    app.dependency_overrides[get_current_claims] = lambda: AsyncMock(
        sub="u_test",
        email="test@example.com",
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as ac:
            yield ac
    finally:
        app.dependency_overrides.clear()


# ── POST /fantasy-scan ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_scan_returns_summary(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """POST scan returns 200 + by_severity summary."""
    tree_id = fake_state["tree_id"]
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-scan",
        json={"rules": None},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tree_id"] == str(tree_id)
    assert body["persons_scanned"] == 10
    assert body["flags_created"] == 5
    assert body["by_severity"]["critical"] == 2


@pytest.mark.asyncio
async def test_post_scan_with_rules_whitelist(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """rules: [...] passes whitelist через в runner."""
    tree_id = fake_state["tree_id"]
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-scan",
        json={"rules": ["birth_after_death", "circular_descent"]},
    )
    assert resp.status_code == 200


# ── GET /fantasy-flags ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_flags_returns_all(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """GET без фильтров → 2 flags из fake_state."""
    tree_id = fake_state["tree_id"]
    resp = await fantasy_client.get(f"/trees/{tree_id}/fantasy-flags")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2


# ── dismiss/undismiss lifecycle ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dismiss_then_undismiss_round_trip(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """Active → dismiss → undismiss возвращает к active."""
    tree_id = fake_state["tree_id"]
    flag_id = fake_state["flag_active_id"]

    # Dismiss.
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-flags/{flag_id}/dismiss",
        json={"reason": "false positive: documented edge case"},
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed_at"] is not None
    assert resp.json()["dismissed_reason"] == "false positive: documented edge case"

    # Undismiss.
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-flags/{flag_id}/undismiss",
    )
    assert resp.status_code == 200
    assert resp.json()["dismissed_at"] is None
    assert resp.json()["dismissed_reason"] is None


@pytest.mark.asyncio
async def test_dismiss_already_dismissed_idempotent(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """POST dismiss на уже-dismissed flag → 200, без повторной mutation."""
    tree_id = fake_state["tree_id"]
    flag_id = fake_state["flag_dismissed_id"]
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-flags/{flag_id}/dismiss",
        json={"reason": "second-time dismissal — no-op"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dismiss_unknown_flag_404(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    tree_id = fake_state["tree_id"]
    bogus = uuid.UUID("99999999-9999-4999-8999-999999999999")
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-flags/{bogus}/dismiss",
        json={"reason": "no such flag"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dismiss_cross_tree_access_404(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """Existing flag, but tree_id mismatch → 404 (no info-leak)."""
    other_tree = uuid.UUID("88888888-8888-4888-8888-888888888888")
    flag_id = fake_state["flag_active_id"]
    resp = await fantasy_client.post(
        f"/trees/{other_tree}/fantasy-flags/{flag_id}/dismiss",
        json={"reason": "wrong tree"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dismiss_empty_reason_rejected_422(
    fantasy_client: AsyncClient,
    fake_state: dict[str, Any],
) -> None:
    """Body validation: reason is required (min_length=1)."""
    tree_id = fake_state["tree_id"]
    flag_id = fake_state["flag_active_id"]
    resp = await fantasy_client.post(
        f"/trees/{tree_id}/fantasy-flags/{flag_id}/dismiss",
        json={"reason": ""},
    )
    assert resp.status_code == 422
