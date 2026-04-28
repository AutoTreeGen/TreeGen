"""Тесты jinja2 рендеринга (en + ru)."""

from __future__ import annotations

import pytest
from email_service.services.templates import render_email
from shared_models.enums import EmailKind

_BASE_CONTEXT = {
    "brand_name": "SmarTreeDNA",
    "support_email": "support@smartreedna.com",
    "web_base_url": "http://localhost:3000",
    "user_display_name": "Alice",
    "locale": "en",
}


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_welcome_renders(locale: str) -> None:
    ctx = {**_BASE_CONTEXT, "locale": locale}
    out = render_email(EmailKind.WELCOME, locale, ctx)
    assert out.subject
    assert "SmarTreeDNA" in out.html_body
    assert "SmarTreeDNA" in out.text_body
    assert "Alice" in out.html_body or "Alice" in out.text_body


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_payment_succeeded_renders_with_amount(locale: str) -> None:
    ctx = {
        **_BASE_CONTEXT,
        "locale": locale,
        "amount_cents": 999,
        "currency": "usd",
        "plan_name": "Pro",
        "billing_period_start": "2026-04-01",
        "billing_period_end": "2026-05-01",
        "invoice_url": "https://stripe.com/invoice/abc",
    }
    out = render_email(EmailKind.PAYMENT_SUCCEEDED, locale, ctx)
    assert "9.99" in out.html_body
    assert "9.99" in out.text_body
    assert "USD" in out.html_body or "usd" in out.html_body.lower()


@pytest.mark.parametrize("locale", ["en", "ru"])
def test_payment_failed_renders(locale: str) -> None:
    ctx = {
        **_BASE_CONTEXT,
        "locale": locale,
        "plan_name": "Pro",
        "next_attempt_at": "2026-05-03",
    }
    out = render_email(EmailKind.PAYMENT_FAILED, locale, ctx)
    assert "settings/billing" in out.html_body
    assert "2026-05-03" in out.html_body or "2026-05-03" in out.text_body


def test_unknown_locale_falls_back_to_en() -> None:
    ctx = {**_BASE_CONTEXT, "locale": "fr"}
    # fr → fallback to en
    out = render_email(EmailKind.WELCOME, "fr", ctx)
    assert "Welcome" in out.html_body


def test_strict_undefined_raises_on_missing_variable() -> None:
    """StrictUndefined: пропущенная переменная → TemplateError."""
    from jinja2 import UndefinedError

    ctx = {**_BASE_CONTEXT, "amount_cents": 100}  # missing currency, plan_name etc.
    with pytest.raises(UndefinedError):
        render_email(EmailKind.PAYMENT_SUCCEEDED, "en", ctx)


def test_html_autoescape() -> None:
    """user_display_name с html-сущностями должен escape'аться."""
    ctx = {**_BASE_CONTEXT, "user_display_name": "<script>alert('x')</script>"}
    out = render_email(EmailKind.WELCOME, "en", ctx)
    assert "<script>" not in out.html_body
    assert "&lt;script&gt;" in out.html_body
