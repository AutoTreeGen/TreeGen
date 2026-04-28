"""Тесты резолюции плана + feature-gating (без Stripe).

Эти тесты pure-async — не требуют HTTP-уровня. Проверяют:

* ``feature_allowed`` — pure-function маппинг feature → bool.
* ``get_user_plan`` — резолвит план из ``stripe_subscriptions`` с учётом
  status, current_period_end, grace period.
* ``assert_feature`` — бросает 402 при отказе.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from billing_service.config import get_settings
from billing_service.services.entitlements import (
    PlanLimits,
    assert_feature,
    feature_allowed,
    get_plan_limits,
    get_user_plan,
)
from fastapi import HTTPException
from shared_models.enums import Plan, SubscriptionStatus
from shared_models.orm import StripeSubscription, User
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def db_session(
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    monkeypatch.setenv("BILLING_SERVICE_BILLING_ENABLED", "true")
    get_settings.cache_clear()
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
    get_settings.cache_clear()


def _make_user_email(suffix: str = "") -> str:
    return f"billing-tests-{uuid.uuid4().hex[:8]}{suffix}@example.com"


# ----- pure-function tests --------------------------------------------------


def test_get_plan_limits_for_free() -> None:
    limits = get_plan_limits(Plan.FREE)
    assert isinstance(limits, PlanLimits)
    assert limits.max_trees == 1
    assert limits.max_persons_per_tree == 100
    assert limits.dna_enabled is False
    assert limits.fs_import_enabled is False


def test_get_plan_limits_for_pro() -> None:
    limits = get_plan_limits(Plan.PRO)
    assert limits.max_trees is None  # unlimited
    assert limits.max_persons_per_tree is None
    assert limits.dna_enabled is True
    assert limits.fs_import_enabled is True


def test_feature_allowed_import_quota_universal() -> None:
    """``import_quota`` доступен на FREE — он гейтит только presence, не количество."""
    assert feature_allowed("import_quota", get_plan_limits(Plan.FREE)) is True
    assert feature_allowed("import_quota", get_plan_limits(Plan.PRO)) is True


def test_feature_allowed_dna_pro_only() -> None:
    assert feature_allowed("dna_enabled", get_plan_limits(Plan.FREE)) is False
    assert feature_allowed("dna_enabled", get_plan_limits(Plan.PRO)) is True


def test_feature_allowed_fs_import_pro_only() -> None:
    assert feature_allowed("fs_import_enabled", get_plan_limits(Plan.FREE)) is False
    assert feature_allowed("fs_import_enabled", get_plan_limits(Plan.PRO)) is True


def test_feature_allowed_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown feature"):
        feature_allowed("not_a_feature", get_plan_limits(Plan.PRO))  # type: ignore[arg-type]


# ----- get_user_plan integration tests --------------------------------------


@pytest.mark.integration
async def test_get_user_plan_no_subscription_returns_free(
    db_session: AsyncSession,
) -> None:
    user = User(email=_make_user_email("-no-sub"), external_auth_id=f"local:{_make_user_email()}")
    db_session.add(user)
    await db_session.flush()

    plan = await get_user_plan(db_session, user.id)
    assert plan is Plan.FREE


@pytest.mark.integration
async def test_get_user_plan_active_pro_subscription(
    db_session: AsyncSession,
) -> None:
    user = User(email=_make_user_email("-active"), external_auth_id=f"local:{_make_user_email()}")
    db_session.add(user)
    await db_session.flush()

    sub = StripeSubscription(
        user_id=user.id,
        stripe_sub_id=f"sub_{uuid.uuid4().hex[:16]}",
        plan=Plan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        current_period_end=dt.datetime.now(dt.UTC) + dt.timedelta(days=15),
        cancel_at_period_end=False,
    )
    db_session.add(sub)
    await db_session.flush()

    plan = await get_user_plan(db_session, user.id)
    assert plan is Plan.PRO


@pytest.mark.integration
async def test_get_user_plan_past_due_within_grace(
    db_session: AsyncSession,
) -> None:
    """PAST_DUE с period_end в окне grace_days → ещё PRO."""
    user = User(
        email=_make_user_email("-past-due-grace"),
        external_auth_id=f"local:{_make_user_email()}",
    )
    db_session.add(user)
    await db_session.flush()

    sub = StripeSubscription(
        user_id=user.id,
        stripe_sub_id=f"sub_{uuid.uuid4().hex[:16]}",
        plan=Plan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        # 1 day ago — внутри 7-day grace по дефолту.
        current_period_end=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        cancel_at_period_end=False,
    )
    db_session.add(sub)
    await db_session.flush()

    plan = await get_user_plan(db_session, user.id)
    assert plan is Plan.PRO


@pytest.mark.integration
async def test_get_user_plan_past_due_after_grace_falls_to_free(
    db_session: AsyncSession,
) -> None:
    user = User(
        email=_make_user_email("-past-due-expired"),
        external_auth_id=f"local:{_make_user_email()}",
    )
    db_session.add(user)
    await db_session.flush()

    sub = StripeSubscription(
        user_id=user.id,
        stripe_sub_id=f"sub_{uuid.uuid4().hex[:16]}",
        plan=Plan.PRO.value,
        status=SubscriptionStatus.PAST_DUE.value,
        # 30 days ago — далеко за пределами 7-day grace.
        current_period_end=dt.datetime.now(dt.UTC) - dt.timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(sub)
    await db_session.flush()

    plan = await get_user_plan(db_session, user.id)
    assert plan is Plan.FREE


@pytest.mark.integration
async def test_get_user_plan_canceled_returns_free(
    db_session: AsyncSession,
) -> None:
    user = User(
        email=_make_user_email("-canceled"),
        external_auth_id=f"local:{_make_user_email()}",
    )
    db_session.add(user)
    await db_session.flush()

    sub = StripeSubscription(
        user_id=user.id,
        stripe_sub_id=f"sub_{uuid.uuid4().hex[:16]}",
        plan=Plan.PRO.value,
        status=SubscriptionStatus.CANCELED.value,
        current_period_end=None,
        cancel_at_period_end=False,
    )
    db_session.add(sub)
    await db_session.flush()

    plan = await get_user_plan(db_session, user.id)
    assert plan is Plan.FREE


@pytest.mark.integration
async def test_assert_feature_blocks_dna_on_free(
    db_session: AsyncSession,
) -> None:
    user = User(
        email=_make_user_email("-assert-dna"),
        external_auth_id=f"local:{_make_user_email()}",
    )
    db_session.add(user)
    await db_session.flush()

    with pytest.raises(HTTPException) as exc:
        await assert_feature(db_session, user.id, "dna_enabled")
    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail["feature"] == "dna_enabled"
    assert detail["current_plan"] == "free"
    assert detail["upgrade_url"].startswith("/pricing")


@pytest.mark.integration
async def test_assert_feature_allows_dna_on_pro(
    db_session: AsyncSession,
) -> None:
    user = User(
        email=_make_user_email("-assert-pro"),
        external_auth_id=f"local:{_make_user_email()}",
    )
    db_session.add(user)
    await db_session.flush()
    sub = StripeSubscription(
        user_id=user.id,
        stripe_sub_id=f"sub_{uuid.uuid4().hex[:16]}",
        plan=Plan.PRO.value,
        status=SubscriptionStatus.ACTIVE.value,
        current_period_end=dt.datetime.now(dt.UTC) + dt.timedelta(days=15),
    )
    db_session.add(sub)
    await db_session.flush()

    # Не должно бросать.
    await assert_feature(db_session, user.id, "dna_enabled")


@pytest.mark.integration
async def test_billing_disabled_returns_pro(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BILLING_ENABLED=false → все юзеры на PRO (dev-bypass)."""
    monkeypatch.setenv("BILLING_SERVICE_BILLING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        # Юзер без записей → должен видеть PRO.
        user = User(
            email=_make_user_email("-bypass"),
            external_auth_id=f"local:{_make_user_email()}",
        )
        db_session.add(user)
        await db_session.flush()
        assert await get_user_plan(db_session, user.id) is Plan.PRO
    finally:
        get_settings.cache_clear()
