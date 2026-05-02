"""Tests for off-catalog evidence weight/provenance split (Phase 22.5 / ADR-0071).

Покрытие:

* Unit (без БД): ``DocumentType`` / ``ProvenanceChannel`` enum'ы,
  ``Provenance`` Pydantic-форма (channel required, jurisdiction
  format, cost_usd non-negative, extra forbidden).
* Property-test (hypothesis): для случайной ``DocumentType``
  weight ∈ {1,2,3}; round-trip Provenance JSON identity.
* Integration (testcontainers-postgres):
  * seed-coverage: каждое значение ``DocumentType`` имеет row в
    ``document_type_weights``;
  * weight derivation: passport→1, gedcom_import→3, dna_match_segment→3;
  * confidence recompute при изменении document_type;
  * Naum-Katz fixture: passport + paid_official_request →
    weight=1, confidence корректно;
  * default provenance: server-default ``{channel: unknown, migrated: true}``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError
from shared_models.enums import DocumentType, ProvenanceChannel
from shared_models.orm import (
    DocumentTypeWeight,
    Evidence,
    Person,
    Tree,
    User,
    reset_document_type_weight_cache,
)
from shared_models.schemas.evidence import Provenance, default_unknown_provenance
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Unit: enums (без БД)
# ---------------------------------------------------------------------------


def test_document_type_enum_includes_required_tiers() -> None:
    """Sanity: enum содержит ключевые значения каждого tier'а + ``other``."""
    values = {dt.value for dt in DocumentType}
    assert "passport" in values  # tier-1
    assert "family_bible" in values  # tier-2
    assert "gedcom_import" in values  # tier-3
    assert "dna_match_segment" in values
    assert "other" in values


def test_provenance_channel_includes_unknown_and_paid_official() -> None:
    """``UNKNOWN`` зарезервирован для backfill; ``PAID_OFFICIAL_REQUEST`` —
    Naum-Katz-style canonical channel."""
    values = {ch.value for ch in ProvenanceChannel}
    assert "unknown" in values
    assert "paid_official_request" in values
    # Anti-drift из ADR-0071: enum не должен содержать «bribery» или
    # подобных формулировок, намекающих на нелегальные платежи.
    assert "bribery" not in values
    assert "bribe" not in values


# ---------------------------------------------------------------------------
# Unit: Provenance Pydantic
# ---------------------------------------------------------------------------


def test_provenance_minimal_explicit_channel() -> None:
    """``channel`` достаточно для валидной Provenance."""
    p = Provenance(channel=ProvenanceChannel.OFFICIAL_REQUEST)
    assert p.channel is ProvenanceChannel.OFFICIAL_REQUEST
    assert p.is_explicit_channel() is True
    assert p.migrated is False


def test_provenance_unknown_is_not_explicit() -> None:
    """``UNKNOWN`` — backfill-only, ``is_explicit_channel`` → False."""
    p = Provenance(channel=ProvenanceChannel.UNKNOWN, migrated=True)
    assert p.is_explicit_channel() is False


def test_provenance_missing_channel_rejected() -> None:
    """Без ``channel`` Pydantic должен ругнуться."""
    with pytest.raises(ValidationError):
        Provenance()  # type: ignore[call-arg]


def test_provenance_extra_field_rejected() -> None:
    """``extra=forbid``: посторонние ключи не пролетают."""
    with pytest.raises(ValidationError):
        Provenance.model_validate(
            {"channel": "official_request", "rogue_field": "x"},
        )


def test_provenance_negative_cost_rejected() -> None:
    """Cost не может быть отрицательным (None допустим — бесплатно)."""
    with pytest.raises(ValidationError):
        Provenance(channel=ProvenanceChannel.PAID_INTERMEDIARY, cost_usd=Decimal("-1"))


def test_provenance_cost_none_allowed() -> None:
    """None cost — норма (значит «бесплатно» / «не релевантно»)."""
    p = Provenance(channel=ProvenanceChannel.FAMILY_ARCHIVE, cost_usd=None)
    assert p.cost_usd is None


def test_provenance_jurisdiction_must_be_iso_alpha2_uppercase() -> None:
    """``jurisdiction`` — две заглавные буквы (ISO 3166-1 alpha-2)."""
    Provenance(channel=ProvenanceChannel.OFFICIAL_REQUEST, jurisdiction="UA")
    with pytest.raises(ValidationError):
        Provenance(channel=ProvenanceChannel.OFFICIAL_REQUEST, jurisdiction="ua")
    with pytest.raises(ValidationError):
        Provenance(channel=ProvenanceChannel.OFFICIAL_REQUEST, jurisdiction="UKR")


def test_default_unknown_provenance_shape() -> None:
    """``default_unknown_provenance`` — backfill-shape с ``migrated=True``."""
    d = default_unknown_provenance()
    assert d == {"channel": "unknown", "migrated": True}
    # Round-trip через Pydantic должен пройти.
    p = Provenance.model_validate(d)
    assert p.channel is ProvenanceChannel.UNKNOWN
    assert p.migrated is True


# ---------------------------------------------------------------------------
# Property-test: round-trip
# ---------------------------------------------------------------------------


@settings(max_examples=50, deadline=None)
@given(
    channel=st.sampled_from([c for c in ProvenanceChannel if c is not ProvenanceChannel.UNKNOWN]),
    cost=st.one_of(st.none(), st.decimals(min_value=0, max_value=10_000, places=2)),
    jurisdiction=st.one_of(
        st.none(),
        st.from_regex(r"^[A-Z]{2}$", fullmatch=True),
    ),
    migrated=st.booleans(),
)
def test_provenance_roundtrip_identity(
    channel: ProvenanceChannel,
    cost: Decimal | None,
    jurisdiction: str | None,
    migrated: bool,
) -> None:
    """Сериализация Provenance в JSON и обратно — identity."""
    original = Provenance(
        channel=channel,
        cost_usd=cost,
        jurisdiction=jurisdiction,
        migrated=migrated,
    )
    payload = original.model_dump(mode="json")
    restored = Provenance.model_validate(payload)
    assert restored == original


# ---------------------------------------------------------------------------
# Integration: testcontainers-postgres
# ---------------------------------------------------------------------------

pytestmark_integration = pytest.mark.integration


async def _seed_owner_and_tree(session: AsyncSession) -> tuple[Tree, Person]:
    """Создать минимальный scope для evidence: tree + person."""
    user = User(
        email="evidence-owner@example.com",
        external_auth_id="auth0|evidence-owner",
        display_name="Evidence Owner",
    )
    session.add(user)
    await session.flush()

    tree = Tree(owner_user_id=user.id, name="Evidence Tree")
    session.add(tree)
    await session.flush()

    person = Person(tree_id=tree.id, primary_name="Naum Katz")
    session.add(person)
    await session.flush()

    return tree, person


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_type_weight_seed_covers_every_enum_member(
    db_session: AsyncSession,
) -> None:
    """Каждое значение ``DocumentType`` имеет row в ``document_type_weights``.

    Защита от рассинхрона enum'а и seed-данных миграции: добавление
    нового DocumentType без soответствующей seed-row сломает FK на
    ``evidence`` при первом INSERT.
    """
    rows = await db_session.execute(
        select(DocumentTypeWeight.document_type, DocumentTypeWeight.weight)
    )
    seeded = dict(rows.all())
    enum_values = {member.value for member in DocumentType}
    missing = enum_values - seeded.keys()
    assert not missing, f"DocumentType members missing seed: {missing}"
    # И все weight'ы — в {1,2,3}.
    assert all(w in {1, 2, 3} for w in seeded.values())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_weight_derivation_passport_tier1(db_session: AsyncSession) -> None:
    """``passport`` → weight=1 (tier-1 government primary)."""
    row = (
        await db_session.execute(
            select(DocumentTypeWeight.weight).where(
                DocumentTypeWeight.document_type == DocumentType.PASSPORT.value,
            )
        )
    ).scalar_one()
    assert row == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_weight_derivation_gedcom_import_tier3(db_session: AsyncSession) -> None:
    """``gedcom_import`` → weight=3 (tier-3 derived)."""
    row = (
        await db_session.execute(
            select(DocumentTypeWeight.weight).where(
                DocumentTypeWeight.document_type == DocumentType.GEDCOM_IMPORT.value,
            )
        )
    ).scalar_one()
    assert row == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_weight_derivation_dna_match_segment_tier3(
    db_session: AsyncSession,
) -> None:
    """``dna_match_segment`` → weight=3 (DNA scoring отдельным pipeline'ом)."""
    row = (
        await db_session.execute(
            select(DocumentTypeWeight.weight).where(
                DocumentTypeWeight.document_type == DocumentType.DNA_MATCH_SEGMENT.value,
            )
        )
    ).scalar_one()
    assert row == 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_default_provenance_is_backfill_unknown(
    db_session: AsyncSession,
) -> None:
    """Server-default provenance — ``{channel: unknown, migrated: true}``.

    Это backfill-shape: API-валидатор должен не пускать такие значения
    от пользователя, но server-default страхует, что DB-row никогда не
    окажется без поля ``channel``.
    """
    reset_document_type_weight_cache()
    _, person = await _seed_owner_and_tree(db_session)

    ev = Evidence(
        tree_id=person.tree_id,
        entity_type="person",
        entity_id=person.id,
        # document_type / match_certainty / provenance — server-default.
    )
    db_session.add(ev)
    await db_session.flush()
    await db_session.refresh(ev)

    assert ev.provenance.get("channel") == "unknown"
    assert ev.provenance.get("migrated") is True
    # weight=3 (other) × match_certainty=0.5 (default) = 1.5
    assert ev.confidence == pytest.approx(1.5)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_naum_katz_passport_fixture(db_session: AsyncSession) -> None:
    """End-to-end Naum-Katz case: passport + paid_official_request → weight=1.

    Фикстура из брифа (Phase 22.5 §INTEGRATION):
    {
      "document_type": "passport",
      "provenance": {
        "channel": "paid_official_request",
        "cost_usd": 100,
        "jurisdiction": "UA",
        "archive_name": "SBU passport file",
        "request_date": "2024-01-15"
      }
    }
    """
    reset_document_type_weight_cache()
    _, person = await _seed_owner_and_tree(db_session)

    provenance = Provenance(
        channel=ProvenanceChannel.PAID_OFFICIAL_REQUEST,
        cost_usd=Decimal("100"),
        jurisdiction="UA",
        archive_name="SBU passport file",
        request_date=dt.date(2024, 1, 15),
    )
    ev = Evidence(
        tree_id=person.tree_id,
        entity_type="person",
        entity_id=person.id,
        document_type=DocumentType.PASSPORT.value,
        match_certainty=0.95,
        provenance=provenance.model_dump(mode="json"),
    )
    db_session.add(ev)
    await db_session.flush()
    await db_session.refresh(ev)

    assert ev.document_type == "passport"
    # weight=1 × match_certainty=0.95 = 0.95
    assert ev.confidence == pytest.approx(0.95)
    # Round-trip provenance через Pydantic.
    p = Provenance.model_validate(ev.provenance)
    assert p.channel is ProvenanceChannel.PAID_OFFICIAL_REQUEST
    assert p.cost_usd == Decimal("100")
    assert p.jurisdiction == "UA"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_confidence_recomputes_on_document_type_change(
    db_session: AsyncSession,
) -> None:
    """Меняем document_type → confidence пересчитывается перед UPDATE.

    Это ключевое поведение брифа: weight всегда derived, никогда не
    задаётся вручную. Меняя document_type, мы автоматически меняем
    confidence без явного recalc-кода.
    """
    reset_document_type_weight_cache()
    _, person = await _seed_owner_and_tree(db_session)

    ev = Evidence(
        tree_id=person.tree_id,
        entity_type="person",
        entity_id=person.id,
        document_type=DocumentType.GEDCOM_IMPORT.value,
        match_certainty=0.6,
        provenance=Provenance(channel=ProvenanceChannel.OTHER).model_dump(mode="json"),
    )
    db_session.add(ev)
    await db_session.flush()
    await db_session.refresh(ev)
    # weight=3 × 0.6 = 1.8
    assert ev.confidence == pytest.approx(1.8)

    ev.document_type = DocumentType.BIRTH_CERTIFICATE.value
    await db_session.flush()
    await db_session.refresh(ev)
    # weight=1 × 0.6 = 0.6
    assert ev.confidence == pytest.approx(0.6)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_provenance_channel_check_constraint(
    db_session: AsyncSession,
) -> None:
    """DB-уровневый CHECK: ``provenance ? 'channel'`` обязателен.

    Это последняя линия defence-in-depth поверх Pydantic-валидации в
    application-layer. Если кто-то из raw SQL вставит provenance без
    ``channel``, INSERT упадёт с IntegrityError.
    """
    from sqlalchemy.exc import IntegrityError

    reset_document_type_weight_cache()
    _, person = await _seed_owner_and_tree(db_session)

    bad = Evidence(
        tree_id=person.tree_id,
        entity_type="person",
        entity_id=person.id,
        provenance={"not_channel": "oops"},
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_match_certainty_range_check(
    db_session: AsyncSession,
) -> None:
    """``match_certainty`` ∈ [0, 1] на DB-уровне."""
    from sqlalchemy.exc import IntegrityError

    reset_document_type_weight_cache()
    _, person = await _seed_owner_and_tree(db_session)

    bad = Evidence(
        tree_id=person.tree_id,
        entity_type="person",
        entity_id=person.id,
        match_certainty=2.0,
        provenance=Provenance(channel=ProvenanceChannel.OTHER).model_dump(mode="json"),
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_evidence_tree_cascade_delete(db_session: AsyncSession) -> None:
    """Удаление дерева каскадно убирает evidence (FK CASCADE).

    GDPR-erasure ADR-0049 удаляет дерево через application-level worker;
    DB-CASCADE — safety net.
    """
    reset_document_type_weight_cache()
    tree, person = await _seed_owner_and_tree(db_session)

    ev = Evidence(
        tree_id=tree.id,
        entity_type="person",
        entity_id=person.id,
        document_type=DocumentType.OTHER.value,
        provenance=Provenance(channel=ProvenanceChannel.OTHER).model_dump(mode="json"),
    )
    db_session.add(ev)
    await db_session.flush()
    ev_id = ev.id
    assert ev_id is not None

    await db_session.delete(tree)
    await db_session.flush()

    remaining = await db_session.execute(select(Evidence).where(Evidence.id == ev_id))
    assert remaining.scalar_one_or_none() is None


# Property: derivation lookup is closed under DocumentType enum.
@settings(max_examples=10, deadline=None)
@given(member=st.sampled_from(list(DocumentType)))
@pytest.mark.integration
@pytest.mark.asyncio
async def test_property_weight_lookup_closed_over_enum(
    db_session: AsyncSession,
    member: DocumentType,
) -> None:
    """Для любой ``DocumentType`` weight в {1,2,3} — закрытость lookup'а."""
    row = (
        await db_session.execute(
            select(DocumentTypeWeight.weight).where(
                DocumentTypeWeight.document_type == member.value,
            )
        )
    ).scalar_one_or_none()
    assert row in {1, 2, 3}, f"missing or invalid weight for {member.value}: {row!r}"
