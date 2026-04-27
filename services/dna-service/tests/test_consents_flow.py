"""Phase 6.2 Task 4 — глубокие consent + upload + match flow-тесты.

Покрывают enforcement-инварианты из ADR-0020:
    - upload требует активный consent;
    - match требует оба consent активны;
    - revoke удаляет blob с диска (hard delete);
    - cross-user matching отвергается (Phase 6.3 territory);
    - DNA_REQUIRE_ENCRYPTION блокирует plaintext.

Все тесты используют synthetic DNA fixtures из Phase 6.0
(packages/dna-analysis/tests/fixtures/synthetic_*.txt) — никаких
реальных rsids / genotypes.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DNA_FIXTURES = _REPO_ROOT / "packages" / "dna-analysis" / "tests" / "fixtures"
_SYNTHETIC_23ANDME = _DNA_FIXTURES / "synthetic_23andme.txt"
_SYNTHETIC_ANCESTRY = _DNA_FIXTURES / "synthetic_ancestry.txt"
_GENETIC_MAP_DIR = _DNA_FIXTURES / "genetic_map"


def _consent_payload(user_id, tree_id, *, email: str = "owner@example.com") -> dict[str, str]:
    return {
        "tree_id": str(tree_id),
        "user_id": str(user_id),
        "kit_owner_email": email,
        "consent_text": "I consent",
    }


async def _create_consent(app_client, user_id, tree_id, **kwargs) -> str:
    resp = await app_client.post("/consents", json=_consent_payload(user_id, tree_id, **kwargs))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _upload_synthetic(app_client, consent_id: str, fixture_path: Path) -> dict[str, object]:
    with fixture_path.open("rb") as fh:
        resp = await app_client.post(
            "/dna-uploads",
            data={"consent_id": consent_id},
            files={"file": (fixture_path.name, fh, "text/plain")},
        )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Upload flow
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_upload_succeeds_with_active_consent(
    app_client, seeded_user_and_tree, storage_root
) -> None:
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    record = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_23ANDME)

    assert record["consent_id"] == consent_id
    assert record["provider"] == "23andme"
    assert record["snp_count"] == 100  # synthetic fixture size
    assert record["encryption_scheme"] == "none"
    # Blob физически на диске.
    blobs = list(storage_root.glob("dna/*.bin"))
    assert len(blobs) == 1
    assert blobs[0].stat().st_size == record["size_bytes"]


@pytest.mark.db
@pytest.mark.integration
async def test_upload_rejected_for_unknown_consent(app_client) -> None:
    fake_consent_id = str(uuid4())
    with _SYNTHETIC_23ANDME.open("rb") as fh:
        resp = await app_client.post(
            "/dna-uploads",
            data={"consent_id": fake_consent_id},
            files={"file": ("x.txt", fh, "text/plain")},
        )
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_upload_rejected_when_consent_revoked(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    revoke_resp = await app_client.delete(f"/consents/{consent_id}")
    assert revoke_resp.status_code == 204

    with _SYNTHETIC_23ANDME.open("rb") as fh:
        upload_resp = await app_client.post(
            "/dna-uploads",
            data={"consent_id": consent_id},
            files={"file": ("x.txt", fh, "text/plain")},
        )
    assert upload_resp.status_code == 409
    assert "revoked" in upload_resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_upload_rejects_unknown_format(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)

    resp = await app_client.post(
        "/dna-uploads",
        data={"consent_id": consent_id},
        files={"file": ("garbage.txt", b"not a DNA file at all", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.db
@pytest.mark.integration
async def test_upload_rejects_empty_file(app_client, seeded_user_and_tree) -> None:
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)

    resp = await app_client.post(
        "/dna-uploads",
        data={"consent_id": consent_id},
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.db
@pytest.mark.integration
async def test_upload_rejected_when_encryption_required(
    app_client, seeded_user_and_tree, monkeypatch
) -> None:
    """С DNA_SERVICE_REQUIRE_ENCRYPTION=true plaintext должен 400'ить.

    Patch get_settings, чтобы не плодить per-test app fixture.
    """
    from dna_service import config

    real_settings = config.get_settings()

    def fake_settings() -> config.Settings:
        return config.Settings(
            database_url=real_settings.database_url,
            storage_root=real_settings.storage_root,
            require_encryption=True,
            max_upload_mb=real_settings.max_upload_mb,
        )

    monkeypatch.setattr(config, "get_settings", fake_settings)

    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    with _SYNTHETIC_23ANDME.open("rb") as fh:
        resp = await app_client.post(
            "/dna-uploads",
            data={"consent_id": consent_id},
            files={"file": ("x.txt", fh, "text/plain")},
        )
    assert resp.status_code == 400
    assert "encryption" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Revocation flow
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_revoke_consent_hard_deletes_blob(
    app_client, seeded_user_and_tree, storage_root
) -> None:
    """Per ADR-0020: revoke удаляет blob с диска и DnaTestRecord row."""
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    record = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_23ANDME)

    # Blob должен существовать перед revoke.
    blobs_before = list(storage_root.glob("dna/*.bin"))
    assert len(blobs_before) == 1

    revoke_resp = await app_client.delete(f"/consents/{consent_id}")
    assert revoke_resp.status_code == 204

    # Blob физически удалён.
    blobs_after = list(storage_root.glob("dna/*.bin"))
    assert blobs_after == []

    # DnaTestRecord row тоже удалён (попытка matching должна 404'ить).
    match_resp = await app_client.post(
        "/matches",
        json={"test_a_id": record["id"], "test_b_id": record["id"]},
    )
    assert match_resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_revoke_unknown_consent_returns_404(app_client) -> None:
    resp = await app_client.delete(f"/consents/{uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Match flow
# ---------------------------------------------------------------------------


@pytest.mark.db
@pytest.mark.integration
async def test_match_succeeds_for_same_user_with_active_consents(
    app_client, seeded_user_and_tree, monkeypatch
) -> None:
    monkeypatch.setenv("DNA_SERVICE_GENETIC_MAP_DIR", str(_GENETIC_MAP_DIR))
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    rec_a = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_23ANDME)
    rec_b = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_ANCESTRY)

    resp = await app_client.post(
        "/matches",
        json={"test_a_id": rec_a["id"], "test_b_id": rec_b["id"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["test_a_provider"] == "23andme"
    assert body["test_b_provider"] == "ancestry"
    assert "shared_segments" in body
    assert "relationship_predictions" in body
    # Cross-platform warning должен быть.
    assert any("Cross-platform" in w for w in body["warnings"])
    # Privacy: ни один сегмент не содержит rsid / genotype.
    for seg in body["shared_segments"]:
        assert set(seg.keys()) == {"chromosome", "start_bp", "end_bp", "num_snps", "cm_length"}


@pytest.mark.db
@pytest.mark.integration
async def test_match_requires_both_consents_active(
    app_client, seeded_user_and_tree, monkeypatch
) -> None:
    monkeypatch.setenv("DNA_SERVICE_GENETIC_MAP_DIR", str(_GENETIC_MAP_DIR))
    user_id, tree_id = seeded_user_and_tree
    consent_a = await _create_consent(app_client, user_id, tree_id, email="a@example.com")
    consent_b = await _create_consent(app_client, user_id, tree_id, email="b@example.com")
    rec_a = await _upload_synthetic(app_client, consent_a, _SYNTHETIC_23ANDME)
    rec_b = await _upload_synthetic(app_client, consent_b, _SYNTHETIC_ANCESTRY)

    # Revoke consent_b (это удалит rec_b сразу) — ожидаем 404 потому что rec_b отсутствует.
    revoke_resp = await app_client.delete(f"/consents/{consent_b}")
    assert revoke_resp.status_code == 204

    resp = await app_client.post(
        "/matches",
        json={"test_a_id": rec_a["id"], "test_b_id": rec_b["id"]},
    )
    # rec_b удалён cascadeом → 404.
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_match_rejected_for_cross_user(
    app_client, seeded_user_and_tree, postgres_dsn, monkeypatch
) -> None:
    """Phase 6.2: cross-user matching отвергается (Phase 6.3 territory)."""
    from shared_models.orm import Tree, User
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    monkeypatch.setenv("DNA_SERVICE_GENETIC_MAP_DIR", str(_GENETIC_MAP_DIR))
    user_a_id, tree_a_id = seeded_user_and_tree

    # Создать второго пользователя + дерево напрямую.
    engine = create_async_engine(postgres_dsn, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = os.urandom(4).hex()
    async with factory() as session, session.begin():
        user_b = User(
            email=f"second-{suffix}@example.com",
            external_auth_id=f"auth0|second-{suffix}",
            display_name="Second User",
        )
        session.add(user_b)
        await session.flush()
        tree_b = Tree(owner_user_id=user_b.id, name="Second Tree")
        session.add(tree_b)
        await session.flush()
        user_b_id, tree_b_id = user_b.id, tree_b.id
    await engine.dispose()

    consent_a = await _create_consent(app_client, user_a_id, tree_a_id)
    consent_b = await _create_consent(app_client, user_b_id, tree_b_id, email="b-user@example.com")
    rec_a = await _upload_synthetic(app_client, consent_a, _SYNTHETIC_23ANDME)
    rec_b = await _upload_synthetic(app_client, consent_b, _SYNTHETIC_ANCESTRY)

    resp = await app_client.post(
        "/matches",
        json={"test_a_id": rec_a["id"], "test_b_id": rec_b["id"]},
    )
    assert resp.status_code == 403
    assert "cross-user" in resp.json()["detail"].lower()


@pytest.mark.db
@pytest.mark.integration
async def test_match_returns_404_for_unknown_record(app_client) -> None:
    resp = await app_client.post(
        "/matches",
        json={"test_a_id": str(uuid4()), "test_b_id": str(uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.db
@pytest.mark.integration
async def test_match_503_when_genetic_map_not_configured(
    app_client, seeded_user_and_tree, monkeypatch
) -> None:
    monkeypatch.delenv("DNA_SERVICE_GENETIC_MAP_DIR", raising=False)
    user_id, tree_id = seeded_user_and_tree
    consent_id = await _create_consent(app_client, user_id, tree_id)
    rec_a = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_23ANDME)
    rec_b = await _upload_synthetic(app_client, consent_id, _SYNTHETIC_ANCESTRY)

    resp = await app_client.post(
        "/matches",
        json={"test_a_id": rec_a["id"], "test_b_id": rec_b["id"]},
    )
    assert resp.status_code == 503
