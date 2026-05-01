"""Phase 10.2b — vision endpoint, status endpoint, per-source $-cap tests.

Маркеры: ``db`` + ``integration`` — testcontainers-postgres + alembic
``upgrade head`` поднимает таблицы.

Реальные Anthropic вызовы замоканы через ``app.dependency_overrides``.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock

import pytest
from ai_layer.clients.anthropic_client import AnthropicCompletion
from ai_layer.types import ExtractionResult, PersonExtract
from PIL import Image

pytestmark = [pytest.mark.db, pytest.mark.integration]


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


async def _reset_extractions_table(postgres_dsn: str) -> None:
    """Очистить таблицы между тестами (testcontainer session-scoped)."""
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
        document_summary="Slonim 1850 birth record (vision).",
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


def _png_upload(width: int = 600, height: int = 400) -> bytes:
    img = Image.new("RGB", (width, height), color="green")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def ai_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_LAYER_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture
def override_extractor(app):
    from parser_service.api.ai_extraction import get_source_extractor

    fake_extractor = AsyncMock()
    fake_extractor.extract_from_text = AsyncMock(return_value=_make_completion())
    fake_extractor.extract_from_image = AsyncMock(return_value=_make_completion())
    # max_tokens — реальный SourceExtractor.max_tokens читается в pre-flight
    # cost-cap'е; mock возвращает int чтобы обойти property-getter.
    fake_extractor.max_tokens = 4096

    app.dependency_overrides[get_source_extractor] = lambda: fake_extractor
    yield fake_extractor
    app.dependency_overrides.pop(get_source_extractor, None)


# ---------------------------------------------------------------------------
# POST /sources/{id}/ai-extract-vision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_vision_endpoint_happy_path(
    app_client, override_extractor, postgres_dsn: str
) -> None:
    """PNG upload → 201 + extraction row + image_was_* поля."""
    await _reset_extractions_table(postgres_dsn)
    override_extractor.extract_from_image.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    assert created.status_code == 201
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    upload = {"image": ("scan.png", _png_upload(), "image/png")}
    resp = await app_client.post(
        f"/sources/{src_id}/ai-extract-vision",
        files=upload,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["fact_count"] == 1
    assert body["extraction"]["status"] == "completed"
    assert body["image_was_resized"] is False  # 600×400 < 2048
    assert body["image_was_rotated"] is False
    assert body["image_original_bytes"] > 0
    assert body["image_processed_bytes"] > 0
    assert body["estimated_cost_usd"] > 0
    # Backend вызвал extract_from_image, а не extract_from_text.
    assert override_extractor.extract_from_image.await_count == 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_vision_endpoint_resizes_large_image(
    app_client, override_extractor, postgres_dsn: str
) -> None:
    """3000-px image → image_was_resized=True."""
    await _reset_extractions_table(postgres_dsn)
    override_extractor.extract_from_image.return_value = _make_completion()

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    upload = {"image": ("big.png", _png_upload(3000, 2000), "image/png")}
    resp = await app_client.post(f"/sources/{src_id}/ai-extract-vision", files=upload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["image_was_resized"] is True
    assert body["image_processed_bytes"] < body["image_original_bytes"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_vision_endpoint_rejects_unsupported_media_type(
    app_client, postgres_dsn: str
) -> None:
    """tiff → 415 (Anthropic vision не принимает)."""
    await _reset_extractions_table(postgres_dsn)
    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    upload = {"image": ("scan.tiff", b"\x49\x49" + b"\x00" * 64, "image/tiff")}
    resp = await app_client.post(f"/sources/{src_id}/ai-extract-vision", files=upload)
    assert resp.status_code == 415


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_vision_endpoint_rejects_corrupt_image(app_client, postgres_dsn: str) -> None:
    """Корявые байты с image/png header → 422 CorruptImageError."""
    await _reset_extractions_table(postgres_dsn)
    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    upload = {"image": ("garbage.png", b"definitely not a png", "image/png")}
    resp = await app_client.post(f"/sources/{src_id}/ai-extract-vision", files=upload)
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_vision_endpoint_rejects_empty_upload(app_client) -> None:
    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    upload = {"image": ("empty.png", b"", "image/png")}
    resp = await app_client.post(f"/sources/{src_id}/ai-extract-vision", files=upload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Per-source $-cap (PARSER_SERVICE_EXTRACT_BUDGET_USD).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_per_source_cost_cap_blocks_pricey_extraction(
    app_client, override_extractor, postgres_dsn: str, monkeypatch
) -> None:
    """EXTRACT_BUDGET_USD=0.0001 → даже маленький запрос упирается в cap → 429.

    ``get_settings()`` без cache (см. ``parser_service.config``); ENV-override
    подхватывается на каждый Depends. Поэтому достаточно monkeypatch.setenv.
    """
    await _reset_extractions_table(postgres_dsn)
    monkeypatch.setenv("PARSER_SERVICE_EXTRACT_BUDGET_USD", "0.0001")

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 429, resp.text
    detail = resp.json()["detail"]
    assert detail["limit_kind"] == "cost_per_source_usd_x10000"
    # Backend НЕ вызвал extract_from_text — cap сработал до этого.
    assert override_extractor.extract_from_text.await_count == 0


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_per_source_cost_cap_zero_disables_gate(
    app_client, override_extractor, postgres_dsn: str, monkeypatch
) -> None:
    """EXTRACT_BUDGET_USD=0 → cap отключён, request проходит."""
    await _reset_extractions_table(postgres_dsn)
    monkeypatch.setenv("PARSER_SERVICE_EXTRACT_BUDGET_USD", "0")
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    resp = await app_client.post(f"/sources/{src_id}/ai-extract", json={})
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# GET /sources/{id}/ai-extract-status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env", "override_extractor")
async def test_status_endpoint_no_runs_yet(app_client, postgres_dsn: str) -> None:
    """Source без extraction'ов — has_extraction=false, остальные nil/0."""
    await _reset_extractions_table(postgres_dsn)
    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]

    resp = await app_client.get(f"/sources/{src_id}/ai-extract-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_extraction"] is False
    assert body["extraction"] is None
    assert body["fact_count"] == 0
    assert body["cost_usd"] == 0.0
    # extract_budget_usd reflects current settings default.
    assert body["extract_budget_usd"] >= 0.0


@pytest.mark.asyncio
@pytest.mark.usefixtures("ai_enabled_env")
async def test_status_endpoint_returns_last_run(
    app_client, override_extractor, postgres_dsn: str
) -> None:
    """После trigger'а — has_extraction=true, fact_count + cost_usd > 0."""
    await _reset_extractions_table(postgres_dsn)
    override_extractor.extract_from_text.return_value = _make_completion(
        persons=[_ok_person()],
        input_tokens=500,
        output_tokens=200,
    )

    files = {"file": ("test.ged", _GED_FOR_AI_EXTRACT, "application/octet-stream")}
    created = await app_client.post("/imports", files=files)
    tree_id = created.json()["tree_id"]
    src_id = (await app_client.get(f"/trees/{tree_id}/sources")).json()["items"][0]["id"]
    await app_client.post(f"/sources/{src_id}/ai-extract", json={})

    resp = await app_client.get(f"/sources/{src_id}/ai-extract-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_extraction"] is True
    assert body["extraction"]["status"] == "completed"
    assert body["fact_count"] == 1
    assert body["cost_usd"] > 0
