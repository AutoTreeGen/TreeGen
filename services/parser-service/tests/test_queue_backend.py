"""Юнит-тесты абстракции ``parser_service.queue.enqueue_job`` (Phase 13.0).

Проверяет диспатч между arq и cloud_tasks по env-флагу
``PARSER_SERVICE_QUEUE_BACKEND``. Не требует Redis / GCP creds —
оба бэкенда мокаются на уровне модуля.

См. ADR-0031.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolate_backend_env() -> Iterator[None]:
    """Сохранить и восстановить ENV между тестами — мы агрессивно его правим."""
    saved = {
        k: os.environ.get(k)
        for k in (
            "PARSER_SERVICE_QUEUE_BACKEND",
            "CLOUD_TASKS_WORKER_BASE_URL",
            "CLOUD_TASKS_INVOKER_SA_EMAIL",
            "CLOUD_TASKS_QUEUE_IMPORTS",
            "CLOUD_TASKS_QUEUE_DEFAULT",
        )
    }
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_resolve_backend_default_is_arq() -> None:
    os.environ.pop("PARSER_SERVICE_QUEUE_BACKEND", None)
    from parser_service.queue import _resolve_backend

    assert _resolve_backend() == "arq"


def test_resolve_backend_unknown_raises() -> None:
    os.environ["PARSER_SERVICE_QUEUE_BACKEND"] = "kafka"
    from parser_service.queue import _resolve_backend

    with pytest.raises(ValueError, match="kafka"):
        _resolve_backend()


@pytest.mark.asyncio
async def test_enqueue_dispatches_to_arq_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ``PARSER_SERVICE_QUEUE_BACKEND=arq`` зовём ``_enqueue_arq``."""
    os.environ["PARSER_SERVICE_QUEUE_BACKEND"] = "arq"
    from parser_service import queue as queue_module

    captured: dict[str, Any] = {}

    async def fake_arq(
        job_name: str,
        *args: Any,
        queue_name: str | None,
        deduplication_key: str | None,
    ) -> None:
        captured["job_name"] = job_name
        captured["args"] = args
        captured["queue_name"] = queue_name
        captured["deduplication_key"] = deduplication_key

    monkeypatch.setattr(queue_module, "_enqueue_arq", fake_arq)

    await queue_module.enqueue_job(
        "run_import_job",
        "job-123",
        "/tmp/file.ged",
        queue_name="imports",
        deduplication_key="import:job-123",
    )

    assert captured == {
        "job_name": "run_import_job",
        "args": ("job-123", "/tmp/file.ged"),
        "queue_name": "imports",
        "deduplication_key": "import:job-123",
    }


@pytest.mark.asyncio
async def test_enqueue_dispatches_to_cloud_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """При ``PARSER_SERVICE_QUEUE_BACKEND=cloud_tasks`` зовём ``_enqueue_cloud_tasks``."""
    os.environ["PARSER_SERVICE_QUEUE_BACKEND"] = "cloud_tasks"
    from parser_service import queue as queue_module

    called = False

    async def fake_ct(
        _job_name: str,
        *_args: Any,
        queue_name: str | None,  # noqa: ARG001 — required by signature
        deduplication_key: str | None,  # noqa: ARG001
    ) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(queue_module, "_enqueue_cloud_tasks", fake_ct)

    await queue_module.enqueue_job("run_import_job", "x", queue_name="imports")
    assert called is True


def test_cloud_tasks_target_url_requires_base() -> None:
    os.environ.pop("CLOUD_TASKS_WORKER_BASE_URL", None)
    from parser_service.queue import _cloud_tasks_target_url

    with pytest.raises(RuntimeError, match="CLOUD_TASKS_WORKER_BASE_URL"):
        _cloud_tasks_target_url("run_import_job")


def test_cloud_tasks_target_url_strips_trailing_slash() -> None:
    os.environ["CLOUD_TASKS_WORKER_BASE_URL"] = "https://parser.run.app/"
    from parser_service.queue import _cloud_tasks_target_url

    url = _cloud_tasks_target_url("run_import_job")
    assert url == "https://parser.run.app/internal/jobs/run_import_job"


def test_cloud_tasks_queue_path_uses_short_name_env() -> None:
    os.environ["CLOUD_TASKS_QUEUE_IMPORTS"] = (
        "projects/p/locations/europe-west1/queues/staging-imports"
    )
    from parser_service.queue import _cloud_tasks_queue_path

    assert _cloud_tasks_queue_path("imports").endswith("staging-imports")


def test_cloud_tasks_queue_path_falls_back_to_default() -> None:
    os.environ.pop("CLOUD_TASKS_QUEUE_IMPORTS", None)
    os.environ["CLOUD_TASKS_QUEUE_DEFAULT"] = (
        "projects/p/locations/europe-west1/queues/staging-default"
    )
    from parser_service.queue import _cloud_tasks_queue_path

    assert _cloud_tasks_queue_path("imports").endswith("staging-default")


def test_cloud_tasks_queue_path_missing_raises() -> None:
    os.environ.pop("CLOUD_TASKS_QUEUE_IMPORTS", None)
    os.environ.pop("CLOUD_TASKS_QUEUE_DEFAULT", None)
    from parser_service.queue import _cloud_tasks_queue_path

    with pytest.raises(RuntimeError, match="CLOUD_TASKS_QUEUE_IMPORTS"):
        _cloud_tasks_queue_path("imports")
