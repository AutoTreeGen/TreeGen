"""Тесты Prometheus metrics endpoint (Phase 9.0).

Smoke-тест ``GET /metrics`` (без БД) — что endpoint вообще отдаёт
exposition format с зарегистрированными treegen_* метриками.

Интеграционные smoke (db + integration markers):

* импорт GEDCOM → ``treegen_import_completed_total{source="gedcom",
  outcome="success"}`` инкремент,
* создание гипотезы → ``treegen_hypothesis_created_total`` инкремент,
* PATCH review → ``treegen_hypothesis_review_action_total{action=...}``
  инкремент.

Дедупный histogram (`dedup_finder_duration_seconds`) покрыт неявно через
hypothesis-create (compute_hypothesis под капотом не зовёт dedup_finder,
но при follow-up bulk-режиме этот collector будет греться). Прямой
smoke на dedup-роуте уже есть в ``test_dedup_api.py``.
"""

from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

# ---- Smoke (no DB) ---------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_endpoint_returns_prometheus_exposition() -> None:
    """``GET /metrics`` отвечает 200 + Prometheus content-type."""
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    # prometheus_client отдаёт CONTENT_TYPE_LATEST — формат менялся между
    # 0.0.4 (старый) и 1.0.0 (current). Проверяем общий контракт:
    # text/plain + явный version-параметр (что Prometheus scraper это
    # распарсит).
    ct = response.headers["content-type"]
    assert ct.startswith("text/plain"), ct
    assert "version=" in ct, ct


@pytest.mark.asyncio
async def test_metrics_body_contains_treegen_collectors() -> None:
    """Body содержит HELP/TYPE строки для всех treegen_* метрик.

    Counter / Histogram появляются в exposition сразу после import
    модуля ``services.metrics`` (его подгружает api.metrics роутер) —
    даже если value = 0.
    """
    from parser_service.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
    body = response.text
    expected_metric_names = (
        "treegen_hypothesis_created_total",
        "treegen_hypothesis_review_action_total",
        "treegen_hypothesis_compute_duration_seconds",
        "treegen_import_completed_total",
        "treegen_dedup_finder_duration_seconds",
    )
    for name in expected_metric_names:
        # Counter в exposition печатается как `<name>_total`, но HELP/TYPE
        # — без `_total`-суффикса (его добавляет рендер). Histogram идёт
        # как `<name>_bucket` / `_count` / `_sum`. Поэтому проверяем
        # просто substring в body — он покрывает оба случая.
        assert name in body, f"missing collector {name} in /metrics body"


# ---- Integration (DB) ------------------------------------------------------


_MINIMAL_GED = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Meir /Zhitnitzky/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 @I2@ INDI
1 NAME Meir /Zhytnicki/
1 SEX M
1 BIRT
2 DATE 1850
2 PLAC Slonim
0 TRLR
"""


def _counter_value(body: str, metric: str, labels: str) -> float:
    """Извлечь value Prometheus counter/sample из exposition строки.

    Формат строки: ``<metric>{<labels>} <value>``. Prometheus sortирует
    labels алфавитно в exposition (не insertion-order), поэтому caller
    может передать labels в любом порядке — мы парсим всю строку метрики
    и проверяем наличие каждой пары ``key="value"`` независимо.

    Возвращает ``0.0`` если линия не найдена — caller решает, считать
    это failure или нет (при свежем процессе counter с label'ами
    появляется только после первого .inc()).
    """
    expected = {
        kv.split("=", 1)[0]: kv.split("=", 1)[1].strip('"')
        for kv in labels.split(",")
        if kv.strip()
    }
    line_pattern = re.compile(
        rf"^{re.escape(metric)}\{{(?P<labels>[^}}]*)\}} (?P<value>\S+)$",
        re.MULTILINE,
    )
    for match in line_pattern.finditer(body):
        actual_labels = {
            kv.split("=", 1)[0]: kv.split("=", 1)[1].strip('"')
            for kv in match.group("labels").split(",")
            if kv.strip()
        }
        if all(actual_labels.get(k) == v for k, v in expected.items()):
            return float(match.group("value"))
    return 0.0


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_import_increments_gedcom_success_counter(app_client) -> None:
    """Импорт GEDCOM → import_completed_total{source=gedcom,outcome=success} +1."""
    before = await app_client.get("/metrics")
    before_value = _counter_value(
        before.text,
        "treegen_import_completed_total",
        'source="gedcom",outcome="success"',
    )

    files = {"file": ("metrics_smoke.ged", _MINIMAL_GED, "application/octet-stream")}
    response = await app_client.post("/imports", files=files)
    assert response.status_code == 201, response.text

    after = await app_client.get("/metrics")
    after_value = _counter_value(
        after.text,
        "treegen_import_completed_total",
        'source="gedcom",outcome="success"',
    )
    assert after_value >= before_value + 1


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_hypothesis_increments_counter(app_client) -> None:
    """POST /trees/{id}/hypotheses → hypothesis_created_total +1."""
    files = {"file": ("metrics_hyp.ged", _MINIMAL_GED, "application/octet-stream")}
    created_import = await app_client.post("/imports", files=files)
    assert created_import.status_code == 201
    tree_id = created_import.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    i1 = next(p for p in items if p["gedcom_xref"] == "I1")["id"]
    i2 = next(p for p in items if p["gedcom_xref"] == "I2")["id"]

    before = await app_client.get("/metrics")
    before_value = _counter_value(
        before.text,
        "treegen_hypothesis_created_total",
        f'rule_id="same_person",tree_id="{tree_id}"',
    )

    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    assert created.status_code == 201, created.text

    after = await app_client.get("/metrics")
    after_value = _counter_value(
        after.text,
        "treegen_hypothesis_created_total",
        f'rule_id="same_person",tree_id="{tree_id}"',
    )
    assert after_value >= before_value + 1


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_review_action_increments_counter(app_client) -> None:
    """PATCH /hypotheses/{id}/review → hypothesis_review_action_total{action=confirmed} +1."""
    files = {"file": ("metrics_review.ged", _MINIMAL_GED, "application/octet-stream")}
    created_import = await app_client.post("/imports", files=files)
    assert created_import.status_code == 201
    tree_id = created_import.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    i1 = next(p for p in items if p["gedcom_xref"] == "I1")["id"]
    i2 = next(p for p in items if p["gedcom_xref"] == "I2")["id"]

    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    hyp_id = created.json()["id"]

    before = await app_client.get("/metrics")
    before_value = _counter_value(
        before.text,
        "treegen_hypothesis_review_action_total",
        'action="confirmed"',
    )

    response = await app_client.patch(
        f"/hypotheses/{hyp_id}/review",
        json={"status": "confirmed", "note": "metrics smoke"},
    )
    assert response.status_code == 200, response.text

    after = await app_client.get("/metrics")
    after_value = _counter_value(
        after.text,
        "treegen_hypothesis_review_action_total",
        'action="confirmed"',
    )
    assert after_value >= before_value + 1


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_dedup_call_records_histogram(app_client) -> None:
    """``GET /trees/{id}/duplicate-suggestions`` → dedup_finder_duration_seconds_count +1."""
    files = {"file": ("metrics_dedup.ged", _MINIMAL_GED, "application/octet-stream")}
    created_import = await app_client.post("/imports", files=files)
    assert created_import.status_code == 201
    tree_id = created_import.json()["tree_id"]

    before = await app_client.get("/metrics")
    before_count = _counter_value(
        before.text,
        "treegen_dedup_finder_duration_seconds_count",
        'entity_type="person"',
    )

    response = await app_client.get(f"/trees/{tree_id}/duplicate-suggestions")
    assert response.status_code == 200, response.text

    after = await app_client.get("/metrics")
    after_count = _counter_value(
        after.text,
        "treegen_dedup_finder_duration_seconds_count",
        'entity_type="person"',
    )
    assert after_count >= before_count + 1


@pytest.mark.db
@pytest.mark.integration
@pytest.mark.asyncio
async def test_compute_hypothesis_records_compose_histogram(app_client) -> None:
    """compose_hypothesis() обёрнут в hypothesis_compute_duration_seconds."""
    files = {"file": ("metrics_compose.ged", _MINIMAL_GED, "application/octet-stream")}
    created_import = await app_client.post("/imports", files=files)
    assert created_import.status_code == 201
    tree_id = created_import.json()["tree_id"]
    listing = await app_client.get(f"/trees/{tree_id}/persons")
    items = listing.json()["items"]
    i1 = next(p for p in items if p["gedcom_xref"] == "I1")["id"]
    i2 = next(p for p in items if p["gedcom_xref"] == "I2")["id"]

    before = await app_client.get("/metrics")
    before_count = _counter_value(
        before.text,
        "treegen_hypothesis_compute_duration_seconds_count",
        'rule_id="compose_default"',
    )

    created = await app_client.post(
        f"/trees/{tree_id}/hypotheses",
        json={
            "subject_a_id": i1,
            "subject_b_id": i2,
            "hypothesis_type": "same_person",
        },
    )
    assert created.status_code == 201

    after = await app_client.get("/metrics")
    after_count = _counter_value(
        after.text,
        "treegen_hypothesis_compute_duration_seconds_count",
        'rule_id="compose_default"',
    )
    assert after_count >= before_count + 1
