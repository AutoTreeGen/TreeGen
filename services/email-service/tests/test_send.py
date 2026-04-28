"""Integration-тесты ``POST /email/send`` с mocked Resend."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from email_service.api.send import set_test_transport
from shared_models.orm import EmailSendLog, User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _make_resend_transport(message_id: str = "re_mock_123") -> httpx.MockTransport:
    """Mock Resend → 200 + {"id": message_id}."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/emails", f"unexpected path: {request.url.path}"
        body = json.loads(request.content)
        assert "to" in body
        assert "subject" in body
        return httpx.Response(200, json={"id": message_id})

    return httpx.MockTransport(handler)


def _make_failing_transport() -> httpx.MockTransport:
    """Mock Resend → 422 (invalid from-domain)."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error": "invalid_from_domain"})

    return httpx.MockTransport(handler)


async def _create_user(
    postgres_dsn: str,
    *,
    locale: str = "en",
    email_opt_out: bool = False,
) -> uuid.UUID:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        user = User(
            email=f"test-{uuid.uuid4().hex[:8]}@example.com",
            external_auth_id=f"local:{uuid.uuid4().hex[:8]}",
            display_name="Test User",
            locale=locale,
            email_opt_out=email_opt_out,
        )
        s.add(user)
        await s.commit()
        await s.refresh(user)
        uid = user.id
    await engine.dispose()
    return uid


@pytest.mark.integration
async def test_send_welcome_happy_path(app_client: object, postgres_dsn: str) -> None:
    user_id = await _create_user(postgres_dsn)
    set_test_transport(_make_resend_transport())

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"welcome:{user_id}",
            "params": {},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "sent"
    assert body["deduplicated"] is False
    assert body["provider_message_id"] == "re_mock_123"


@pytest.mark.integration
async def test_send_idempotent_returns_cached(
    app_client: object,
    postgres_dsn: str,
) -> None:
    """Повторный POST с тем же idempotency_key → cached result."""
    user_id = await _create_user(postgres_dsn)
    set_test_transport(_make_resend_transport(message_id="re_first_send"))
    key = f"welcome:{user_id}-dedup"

    payload = {
        "kind": "welcome",
        "recipient_user_id": str(user_id),
        "idempotency_key": key,
        "params": {},
    }

    r1 = await app_client.post("/email/send", json=payload)  # type: ignore[attr-defined]
    assert r1.status_code == 200
    assert r1.json()["deduplicated"] is False
    assert r1.json()["provider_message_id"] == "re_first_send"

    # Подменим transport на сломанный — если бы dispatch сделал второй
    # вызов, тест упал бы.
    set_test_transport(_make_failing_transport())

    r2 = await app_client.post("/email/send", json=payload)  # type: ignore[attr-defined]
    assert r2.status_code == 200
    body = r2.json()
    assert body["deduplicated"] is True
    assert body["provider_message_id"] == "re_first_send"  # тот же id, не failed


@pytest.mark.integration
async def test_send_opt_out_skipped(
    app_client: object,
    postgres_dsn: str,
) -> None:
    user_id = await _create_user(postgres_dsn, email_opt_out=True)
    # Если бы dispatch вызвал Resend, тест упал бы (failing transport).
    set_test_transport(_make_failing_transport())

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"welcome:optout:{user_id}",
            "params": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "skipped_optout"
    assert body["provider_message_id"] is None


@pytest.mark.integration
async def test_send_unknown_user_returns_404(app_client: object) -> None:
    set_test_transport(_make_resend_transport())
    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": "00000000-0000-0000-0000-000000000000",
            "idempotency_key": "welcome:nonexistent",
            "params": {},
        },
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_send_invalid_kind_returns_422(
    app_client: object,
    postgres_dsn: str,
) -> None:
    user_id = await _create_user(postgres_dsn)
    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "not_a_real_kind",
            "recipient_user_id": str(user_id),
            "idempotency_key": "x",
            "params": {},
        },
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_send_redacts_dna_params_into_log(
    app_client: object,
    postgres_dsn: str,
) -> None:
    """Caller случайно прислал DNA-поле → в БД [redacted]."""
    user_id = await _create_user(postgres_dsn)
    set_test_transport(_make_resend_transport())

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"welcome:dna-leak:{user_id}",
            "params": {"shared_cm": 999, "kit_id": "secret"},
        },
    )
    assert response.status_code == 200

    # Проверяем log row напрямую — params должны быть redacted.
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        log = (
            await s.execute(
                select(EmailSendLog).where(
                    EmailSendLog.idempotency_key == f"welcome:dna-leak:{user_id}",
                )
            )
        ).scalar_one()
        assert log.params == {
            "shared_cm": "[redacted]",
            "kit_id": "[redacted]",
        }
    await engine.dispose()


@pytest.mark.integration
async def test_send_resend_failure_persists_failed_status(
    app_client: object,
    postgres_dsn: str,
) -> None:
    user_id = await _create_user(postgres_dsn)
    set_test_transport(_make_failing_transport())

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"welcome:fail:{user_id}",
            "params": {},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] is not None
    assert body["provider_message_id"] is None


@pytest.mark.integration
async def test_send_locale_ru_uses_ru_template_path(
    app_client: object,
    postgres_dsn: str,
) -> None:
    """Phase 12.2a: ru-локаль резолвит шаблон ``ru.html`` (пока копия en)."""
    user_id = await _create_user(postgres_dsn, locale="ru")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["subject"] = body["subject"]
        captured["html"] = body["html"]
        return httpx.Response(200, json={"id": "re_ru"})

    set_test_transport(httpx.MockTransport(handler))

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "welcome",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"welcome:ru:{user_id}",
            "params": {},
        },
    )
    assert response.status_code == 200
    # 12.2a ships ru = en copy; just assert template resolved without error.
    # 12.2b будет асертить специфичный ru-текст («Здравствуйте» и т.п.).
    assert "SmarTreeDNA" in captured["html"]


@pytest.mark.integration
async def test_send_disabled_flag_skips(
    app_client: object,
    postgres_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EMAIL_SERVICE_ENABLED=false → status=skipped_optout без Resend."""
    from email_service.config import get_settings

    user_id = await _create_user(postgres_dsn)
    monkeypatch.setenv("EMAIL_SERVICE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        set_test_transport(_make_failing_transport())  # должна не вызываться
        response = await app_client.post(  # type: ignore[attr-defined]
            "/email/send",
            json={
                "kind": "welcome",
                "recipient_user_id": str(user_id),
                "idempotency_key": f"welcome:flagoff:{user_id}",
                "params": {},
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "skipped_optout"
    finally:
        get_settings.cache_clear()


@pytest.mark.integration
async def test_send_payment_succeeded_with_full_params(
    app_client: object,
    postgres_dsn: str,
) -> None:
    user_id = await _create_user(postgres_dsn)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured["html"] = body["html"]
        return httpx.Response(200, json={"id": "re_pay"})

    set_test_transport(httpx.MockTransport(handler))

    response = await app_client.post(  # type: ignore[attr-defined]
        "/email/send",
        json={
            "kind": "payment_succeeded",
            "recipient_user_id": str(user_id),
            "idempotency_key": f"evt_test_{uuid.uuid4().hex[:8]}",
            "params": {
                "amount_cents": 1500,
                "currency": "usd",
                "plan_name": "Pro",
                "billing_period_start": "2026-04-01",
                "billing_period_end": "2026-05-01",
                "invoice_url": "https://stripe.com/i/123",
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert "15.00" in captured["html"]
