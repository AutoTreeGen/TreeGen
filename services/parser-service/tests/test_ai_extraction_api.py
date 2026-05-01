"""Phase 10.2 / ADR-0059 — AI source extraction API integration tests.

Маркеры: ``db`` + ``integration`` — testcontainers-postgres + alembic
``upgrade head`` поднимает source_extractions/extracted_facts таблицы.

Тесты:

* Happy path: trigger → list → accept → reject.
* Kill-switch: AI_LAYER_ENABLED=false → 503.
* DNA-source forbidden: source_type=dna_test → 422.
* Rate limit: 11 вызовов подряд → 429 на 11-м.
* Budget exceeded: token-budget превышен → 429.
* Source без text_excerpt и без document_text в body → 422.

Реальные Anthropic вызовы замоканы через ``app.dependency_overrides``:
подменяем ``get_source_extractor`` на stub, возвращающий заранее
заданный ``ExtractionResult``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from ai_layer.clients.anthropic_client import AnthropicCompletion
from ai_layer.types import ExtractionResult, PersonExtract

pytestmark = [pytest.mark.db, pytest.mark.integration]


# Минимальный GEDCOM с одним SOUR + INDI с TEXT-блоком, чтобы Source
# имел text_excerpt для extraction'а.
_GED_FOR_AI_EXTRACT = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
1 SEX M
0 @S1@ SOUR
1 TITL Slonim parish register 1850
1 AUTH Russian Orthodox Church
1 ABBR Slonim1850
1 TEXT John Smith born 1850 in Slonim, son of Peter Smith.
0 TRLR
"""


async def _mark_source_as_dna(postgres_dsn: str, source_id: str) -> None:
    """Set ``Source.source_type='dna_test'`` directly via SQL.

    GEDCOM-parser не маппит ``1 TYPE dna_test`` на SOUR-record (на 5.5.5
    spec'у этот tag к SOUR не относится). Чтобы протестировать privacy-
    gate, выставляем source_type через прямой UPDATE — так же как это
    сделает manual user input в UI Phase 10.2b.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE sources SET source_type = 'dna_test' WHERE id = :id"),
            {"id": source_id},
        )
    await engine.dispose()


async def _reset_extractions_table(postgres_dsn: str) -> None:
    """Удалить все source_extractions (CASCADE → extracted_facts).

    Postgres testcontainer общий между тестами (session-scoped), поэтому
    budget-тесты должны начинать с чистого листа: иначе расход от
    предыдущих тестов накапливается и сломает rate-limit threshold.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM source_extractions"))
    await engine.dispose()


def _make_completion(
    *,
    persons: list[PersonExtract] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> AnthropicCompletion[ExtractionResult]:
    parsed = ExtractionResult(
        persons=persons or [],
        events=[],
        relationships=[],
        document_summary="Slonim 1850 birth record.",
        overall_confidence=0.85,
        language_detected="en",
    )
    return AnthropicCompletion(
        parsed=parsed,
        model="claude-sonnet-4-6",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason="end_turn",
    )


def _ok_person() -> PersonExtract:
    return PersonExtract(
        full_name="John Smith",
        given_name="John",
        surname="Smith",
        sex="M",
        birth_date_raw="1850",
        birth_place_raw="Slonim",
        death_date_raw=None,
        death_place_raw=None,
        relationship_hints=["son of Peter Smith"],
        raw_quote="John Smith born 1850 in Slonim",
        confidence=0.9,
    )


@pytest.fixture
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Включить AI_LAYER_ENABLED=true для теста + provide fake API key."""
    monkeypatch.setenv("AI_LAYER_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture
def override_extractor(app):
    """Подменить get_source_extractor на stub.

    Yields AsyncMock-instance, которому caller присваивает
    ``.extract_from_text.return_value`` (или ``.side_effect``).
    """
    from parser_service.api.ai_extraction import get_source_extractor

    fake_extractor = AsyncMock()
    # extract_from_text — async-метод на extractor instance.
    fake_extractor.extract_from_text = AsyncMock(return_value=_make_completion())
    # Phase 10.2b: pre-flight cost-cap читает .max_tokens — без явного
    # int'а AsyncMock вернёт MagicMock и cap-проверка упадёт TypeError'ом.
    fake_extractor.max_tokens = 4096

    app.dependency_overrides[get_source_extractor] = lambda: fake_extractor
    yield fake_extractor
    app.dependency_overrides.pop(get_source_extractor, None)


# -----------------------------------------------------------------------------
# Happy path.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_trigger_extract_happy_path(
    app_client, override_extractor, postgres_dsn: str
) -> None:
    """POST /sources/{id}/ai-extract → 201 + extraction row + facts."""
    await _reset_extractions_table(postgres_dsn)
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201, created.text
    tree_id = created.json()["tree_id"]

    listing = await app_client.get(f"/trees/{tree_id}/sources")
    src_id = listing.json()["items"][0]["id"]

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["fact_count"] == 1
    assert body["extraction"]["status"] == "completed"
    assert body["extraction"]["input_tokens"] == 100
    assert body["extraction"]["output_tokens"] == 50
    assert body["budget_remaining_runs"] == 9  # default 10/day, 1 used


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_list_and_accept_extracted_fact(app_client, override_extractor) -> None:
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    trigger = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert trigger.status_code == 201

    listing = await app_client.get(f"/sources/{src_id}/extracted-facts")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert len(body["extractions"]) == 1
    assert len(body["facts"]) == 1
    fact = body["facts"][0]
    assert fact["fact_kind"] == "person"
    assert fact["status"] == "pending"
    assert fact["data"]["full_name"] == "John Smith"

    # Accept.
    accept_resp = await app_client.post(
        f"/sources/{src_id}/extracted-facts/{fact['id']}/accept",
        json={"note": "имя совпадает с дедушкой"},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["status"] == "accepted"
    assert accept_resp.json()["review_note"] == "имя совпадает с дедушкой"

    # Повторный accept на already-accepted → 409.
    second = await app_client.post(
        f"/sources/{src_id}/extracted-facts/{fact['id']}/accept",
        json={},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_reject_extracted_fact(app_client, override_extractor) -> None:
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    facts = (await app_client.get(f"/sources/{src_id}/extracted-facts")).json()["facts"]
    fid = facts[0]["id"]

    reject_resp = await app_client.post(
        f"/sources/{src_id}/extracted-facts/{fid}/reject",
        json={"note": "duplicate of existing person"},
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "rejected"


# -----------------------------------------------------------------------------
# Kill-switch.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("override_extractor")
async def test_kill_switch_returns_503(app_client, monkeypatch) -> None:
    """AI_LAYER_ENABLED=false → 503."""
    monkeypatch.setenv("AI_LAYER_ENABLED", "false")

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 503
    assert "AI layer is disabled" in resp.json()["detail"]


# -----------------------------------------------------------------------------
# DNA privacy.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_dna_source_forbidden_returns_422(app_client, postgres_dsn: str) -> None:
    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    await _mark_source_as_dna(postgres_dsn, src_id)

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 422, resp.text
    assert "DNA-marked" in resp.json()["detail"]


# -----------------------------------------------------------------------------
# Budget guard.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_rate_limit_triggers_429(
    app_client, override_extractor, monkeypatch, postgres_dsn: str
) -> None:
    """11-й вызов подряд → 429 (default rate limit = 10)."""
    # Уменьшим лимит до 2 чтобы не делать 11 вызовов в тесте.
    # ``get_budget_limits`` зависимость не кешируется — pydantic-settings
    # перечитывает env на каждый ``Settings()`` инстанциат.
    monkeypatch.setenv("PARSER_SERVICE_AI_MAX_RUNS_PER_DAY", "2")
    await _reset_extractions_table(postgres_dsn)

    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    # Два вызова — оба ок.
    for _ in range(2):
        resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
        assert resp.status_code == 201, resp.text

    # Третий — 429.
    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail["limit_kind"] == "runs_per_day"
    assert detail["limit_value"] == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_token_budget_exceeded_returns_429(
    app_client, override_extractor, monkeypatch, postgres_dsn: str
) -> None:
    """Tokens-month budget превышен → 429 (limit_kind=tokens_per_month)."""
    monkeypatch.setenv("PARSER_SERVICE_AI_MAX_TOKENS_PER_MONTH", "100")
    monkeypatch.setenv("PARSER_SERVICE_AI_MAX_RUNS_PER_DAY", "0")  # отключим rate-limit
    await _reset_extractions_table(postgres_dsn)

    # Возвращаем completion с 200 токенами — превысит лимит за один call.
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
        input_tokens=150,
        output_tokens=60,
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    # Первый вызов проходит (на момент проверки usage был 0).
    first = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert first.status_code == 201

    # Второй — 429, потому что после первого набралось 210 tokens > 100.
    second = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert second.status_code == 429
    assert second.json()["detail"]["limit_kind"] == "tokens_per_month"


# -----------------------------------------------------------------------------
# Edge cases.
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_no_text_returns_422(app_client) -> None:
    """Source без text_excerpt и без document_text в body → 422."""
    # GEDCOM без TEXT — Source.text_excerpt = None.
    ged_no_text = b"""\
0 HEAD
1 SOUR test
1 GEDC
2 VERS 5.5.5
2 FORM LINEAGE-LINKED
1 CHAR UTF-8
0 @I1@ INDI
1 NAME John /Smith/
0 @S1@ SOUR
1 TITL Empty source
0 TRLR
"""
    files = {"file": ("test.ged", ged_no_text, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 422, resp.text
    assert "no text content" in resp.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_explicit_document_text_overrides_text_excerpt(
    app_client, override_extractor
) -> None:
    """Caller передал document_text → используется он, не text_excerpt."""
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    document_text = "Different content from PDF: Mary Jones born 1900 in Vilna."
    resp = await app_client.post(
        f"/sources/{src_id}/ai-extract",
        json={"document_text": document_text},
    )
    assert resp.status_code == 201, resp.text

    # Проверим, что extractor был вызван с переданным текстом.
    call_args = override_extractor.extract_from_text.call_args
    assert call_args.args[0] == document_text
