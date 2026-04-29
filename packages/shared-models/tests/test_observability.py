"""Юнит-тесты ``shared_models.observability`` (Phase 13.1).

Покрывают:

* JSON-formatter — корректный severity, message, extra-поля.
* ``init_sentry`` no-op при пустом ``SENTRY_DSN``.
* ``configure_json_logging`` — вкл/выкл по env-флагу.

Без сети и без sentry-sdk: оба пути работают на чистом stdlib.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from shared_models.observability import (
    CloudLoggingJSONFormatter,
    configure_json_logging,
    init_sentry,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Снять handler'ы между тестами — иначе `configure_json_logging` накапливается."""
    saved_level = logging.getLogger().level
    saved_handlers = list(logging.getLogger().handlers)
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def _make_record(
    *,
    msg: str = "hello",
    level: int = logging.INFO,
    extra: dict[str, Any] | None = None,
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_json_formatter_basic_fields() -> None:
    formatter = CloudLoggingJSONFormatter(service_name="parser-service")
    record = _make_record(msg="job started")
    out = json.loads(formatter.format(record))

    assert out["severity"] == "INFO"
    assert out["message"] == "job started"
    assert out["service"] == "parser-service"
    assert out["logger"] == "test.logger"


def test_json_formatter_includes_extra() -> None:
    formatter = CloudLoggingJSONFormatter(service_name="parser-service")
    record = _make_record(extra={"event": "import_failed", "import_job_id": "abc-123"})
    out = json.loads(formatter.format(record))

    assert out["event"] == "import_failed"
    assert out["import_job_id"] == "abc-123"


def test_json_formatter_handles_non_serializable_extra() -> None:
    formatter = CloudLoggingJSONFormatter(service_name="x")

    class Weird:
        def __repr__(self) -> str:
            return "<Weird>"

    record = _make_record(extra={"obj": Weird()})
    out = json.loads(formatter.format(record))
    assert out["obj"] == "<Weird>"


def test_init_sentry_noop_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry(service_name="parser-service") is False


def test_init_sentry_noop_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://dummy@dsn.example/1")
    # Подсунуть отсутствующий импорт через sys.modules.
    import sys

    saved = sys.modules.pop("sentry_sdk", None)
    sys.modules["sentry_sdk"] = None  # type: ignore[assignment]
    try:
        assert init_sentry(service_name="parser-service") is False
    finally:
        sys.modules.pop("sentry_sdk", None)
        if saved is not None:
            sys.modules["sentry_sdk"] = saved


def test_configure_json_logging_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_FORMAT_JSON", raising=False)
    root = logging.getLogger()
    handler_count_before = len(root.handlers)
    configure_json_logging(service_name="parser-service")
    assert len(root.handlers) == handler_count_before  # no-op


def test_configure_json_logging_explicit_enabled() -> None:
    configure_json_logging(service_name="parser-service", enabled=True)
    root = logging.getLogger()
    assert any(
        isinstance(h.formatter, CloudLoggingJSONFormatter)
        for h in root.handlers
        if h.formatter is not None
    )


def test_configure_json_logging_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT_JSON", "true")
    configure_json_logging(service_name="dna-service")
    root = logging.getLogger()
    formatters = [h.formatter for h in root.handlers]
    assert any(
        isinstance(f, CloudLoggingJSONFormatter) and f.service_name == "dna-service"
        for f in formatters
    )
