"""Integration тесты `GET /trees/{id}/relationships/{kind}/{a}/{b}/evidence` (Phase 15.1).

Покрытие:

* parent_child / spouse / sibling — happy paths.
* contradicting evidence через Hypothesis с negative direction.
* empty supporting + naive_count fallback (no hypothesis).
* 404 unknown relationship_id.
* 403 для пользователя без membership.
* 400 self-loop (subject_id == object_id).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
import pytest_asyncio
from shared_models import TreeRole
from shared_models.orm import (
    Citation,
    Family,
    FamilyChild,
    Hypothesis,
    HypothesisEvidence,
    Person,
    Source,
    Tree,
    TreeMembership,
    User,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.db, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Helpers — direct ORM seeding для детерминированных fixture-сценариев.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(postgres_dsn: str) -> Any:
    engine = create_async_engine(postgres_dsn, future=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _make_user(factory: Any, *, email: str | None = None) -> User:
    e = email or f"rel-{uuid.uuid4().hex[:8]}@example.com"
    async with factory() as session:
        user = User(
            email=e,
            external_auth_id=f"local:{e}",
            display_name=e.split("@", 1)[0],
            locale="en",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_tree(factory: Any, *, owner: User) -> Tree:
    async with factory() as session:
        tree = Tree(
            owner_user_id=owner.id,
            name=f"Rel Tree {uuid.uuid4().hex[:6]}",
            visibility="private",
            default_locale="en",
            settings={},
            provenance={},
            version_id=1,
        )
        session.add(tree)
        await session.flush()
        m = TreeMembership(
            tree_id=tree.id,
            user_id=owner.id,
            role=TreeRole.OWNER.value,
            accepted_at=dt.datetime.now(dt.UTC),
        )
        session.add(m)
        await session.commit()
        await session.refresh(tree)
        return tree


async def _make_person(factory: Any, *, tree: Tree) -> Person:
    async with factory() as session:
        person = Person(tree_id=tree.id)
        session.add(person)
        await session.commit()
        await session.refresh(person)
        return person


async def _make_family(
    factory: Any,
    *,
    tree: Tree,
    husband: Person | None = None,
    wife: Person | None = None,
    provenance: dict[str, Any] | None = None,
) -> Family:
    async with factory() as session:
        family = Family(
            tree_id=tree.id,
            husband_id=husband.id if husband else None,
            wife_id=wife.id if wife else None,
            provenance=provenance or {},
        )
        session.add(family)
        await session.commit()
        await session.refresh(family)
        return family


async def _add_child(
    factory: Any,
    *,
    family: Family,
    child: Person,
) -> None:
    async with factory() as session:
        fc = FamilyChild(
            family_id=family.id,
            child_person_id=child.id,
        )
        session.add(fc)
        await session.commit()


async def _make_source_with_citation(
    factory: Any,
    *,
    tree: Tree,
    family: Family,
    title: str,
    repository: str | None = None,
    page: str | None = None,
    snippet: str | None = None,
    quality: float = 0.7,
) -> tuple[Source, Citation]:
    async with factory() as session:
        source = Source(
            tree_id=tree.id,
            title=title,
            repository=repository,
        )
        session.add(source)
        await session.flush()
        citation = Citation(
            tree_id=tree.id,
            source_id=source.id,
            entity_type="family",
            entity_id=family.id,
            page_or_section=page,
            quoted_text=snippet,
            quality=quality,
        )
        session.add(citation)
        await session.commit()
        await session.refresh(source)
        await session.refresh(citation)
        return source, citation


async def _make_hypothesis_with_evidences(
    factory: Any,
    *,
    tree: Tree,
    hypothesis_type: str,
    person_a: Person,
    person_b: Person,
    composite_score: float,
    supports_count: int = 0,
    contradicts_count: int = 0,
) -> Hypothesis:
    """Гипотеза + N supports + M contradicts evidences."""
    async with factory() as session:
        # Canonical ordering (a < b) per UniqueConstraint.
        if str(person_a.id) > str(person_b.id):
            person_a, person_b = person_b, person_a
        hyp = Hypothesis(
            tree_id=tree.id,
            hypothesis_type=hypothesis_type,
            subject_a_type="person",
            subject_a_id=person_a.id,
            subject_b_type="person",
            subject_b_id=person_b.id,
            composite_score=composite_score,
            rules_version="test-v1",
            provenance={},
            version_id=1,
        )
        session.add(hyp)
        await session.flush()
        for i in range(supports_count):
            session.add(
                HypothesisEvidence(
                    hypothesis_id=hyp.id,
                    rule_id=f"rule_supports_{i}",
                    direction="supports",
                    weight=0.6,
                    observation=f"Supports observation {i}",
                )
            )
        for i in range(contradicts_count):
            session.add(
                HypothesisEvidence(
                    hypothesis_id=hyp.id,
                    rule_id=f"rule_contradicts_{i}",
                    direction="contradicts",
                    weight=0.4,
                    observation=f"Contradicts observation {i}",
                )
            )
        await session.commit()
        await session.refresh(hyp)
        return hyp


def _hdr(user: User) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}


# ---------------------------------------------------------------------------
# parent_child
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_child_happy_path_with_family_citation(
    app_client, session_factory: Any
) -> None:
    """Owner запрашивает evidence для parent→child link → видит Source on family."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    parent = await _make_person(session_factory, tree=tree)
    child = await _make_person(session_factory, tree=tree)
    family = await _make_family(
        session_factory,
        tree=tree,
        husband=parent,
        provenance={
            "source_files": ["test.ged"],
            "import_job_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    await _add_child(session_factory, family=family, child=child)
    source, _ = await _make_source_with_citation(
        session_factory,
        tree=tree,
        family=family,
        title="Birth register 1850",
        repository="State Archive of Grodno",
        page="folio 42",
        snippet="Sigmund b. of Anna",
        quality=0.8,
    )

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/parent_child/{parent.id}/{child.id}/evidence",
        headers=_hdr(owner),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relationship"]["kind"] == "parent_child"
    assert body["relationship"]["subject_person_id"] == str(parent.id)
    assert body["relationship"]["object_person_id"] == str(child.id)
    assert len(body["supporting"]) == 1
    s = body["supporting"][0]
    assert s["source_id"] == str(source.id)
    assert s["title"] == "Birth register 1850"
    assert s["repository"] == "State Archive of Grodno"
    assert s["citation"] == "folio 42"
    assert s["snippet"] == "Sigmund b. of Anna"
    assert s["reliability"] == pytest.approx(0.8)
    assert s["kind"] == "citation"
    assert body["contradicting"] == []
    # Confidence: no hypothesis → naive_count, score=1.0 (1 supp / 1 total).
    assert body["confidence"]["method"] == "naive_count"
    assert body["confidence"]["score"] == pytest.approx(1.0)
    # Provenance aggregated from family.
    assert body["provenance"]["source_files"] == ["test.ged"]
    assert body["provenance"]["import_job_id"] == "00000000-0000-0000-0000-000000000099"


@pytest.mark.asyncio
async def test_parent_child_with_hypothesis_uses_bayesian_method(
    app_client, session_factory: Any
) -> None:
    """Hypothesis на parent_child → confidence.method='bayesian_fusion_v2'."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    parent = await _make_person(session_factory, tree=tree)
    child = await _make_person(session_factory, tree=tree)
    family = await _make_family(session_factory, tree=tree, husband=parent)
    await _add_child(session_factory, family=family, child=child)
    await _make_hypothesis_with_evidences(
        session_factory,
        tree=tree,
        hypothesis_type="parent_child",
        person_a=parent,
        person_b=child,
        composite_score=0.87,
        supports_count=2,
        contradicts_count=1,
    )

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/parent_child/{parent.id}/{child.id}/evidence",
        headers=_hdr(owner),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 2 supports — оба inference rules; 0 citations on family.
    assert len(body["supporting"]) == 2
    assert all(s["kind"] == "inference_rule" for s in body["supporting"])
    assert len(body["contradicting"]) == 1
    assert body["contradicting"][0]["kind"] == "inference_rule"
    assert body["contradicting"][0]["rule_id"] == "rule_contradicts_0"
    assert body["confidence"]["method"] == "bayesian_fusion_v2"
    assert body["confidence"]["score"] == pytest.approx(0.87)
    assert body["confidence"]["hypothesis_id"] is not None


# ---------------------------------------------------------------------------
# spouse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spouse_happy_path_symmetric(app_client, session_factory: Any) -> None:
    """SPOUSE симметричен: и (a,b), и (b,a) дают тот же результат."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    husband = await _make_person(session_factory, tree=tree)
    wife = await _make_person(session_factory, tree=tree)
    family = await _make_family(session_factory, tree=tree, husband=husband, wife=wife)
    await _make_source_with_citation(
        session_factory,
        tree=tree,
        family=family,
        title="Marriage record",
        page="vol 3 p. 17",
    )

    r1 = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{husband.id}/{wife.id}/evidence",
        headers=_hdr(owner),
    )
    r2 = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{wife.id}/{husband.id}/evidence",
        headers=_hdr(owner),
    )

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert len(r1.json()["supporting"]) == 1
    assert len(r2.json()["supporting"]) == 1
    assert r1.json()["supporting"][0]["title"] == "Marriage record"


# ---------------------------------------------------------------------------
# sibling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sibling_happy_path(app_client, session_factory: Any) -> None:
    """Два children одной family → sibling."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    parent = await _make_person(session_factory, tree=tree)
    child_a = await _make_person(session_factory, tree=tree)
    child_b = await _make_person(session_factory, tree=tree)
    family = await _make_family(session_factory, tree=tree, husband=parent)
    await _add_child(session_factory, family=family, child=child_a)
    await _add_child(session_factory, family=family, child=child_b)
    await _make_source_with_citation(
        session_factory,
        tree=tree,
        family=family,
        title="Family bible",
    )

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/sibling/{child_a.id}/{child_b.id}/evidence",
        headers=_hdr(owner),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relationship"]["kind"] == "sibling"
    assert len(body["supporting"]) == 1
    assert body["supporting"][0]["title"] == "Family bible"


# ---------------------------------------------------------------------------
# Empty / 404 / 403 / 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_supporting_naive_count_score_zero(app_client, session_factory: Any) -> None:
    """Family без citations → supporting=[], confidence.score=0.0 naive."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    parent = await _make_person(session_factory, tree=tree)
    child = await _make_person(session_factory, tree=tree)
    family = await _make_family(session_factory, tree=tree, husband=parent)
    await _add_child(session_factory, family=family, child=child)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/parent_child/{parent.id}/{child.id}/evidence",
        headers=_hdr(owner),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["supporting"] == []
    assert body["contradicting"] == []
    assert body["confidence"]["method"] == "naive_count"
    assert body["confidence"]["score"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_returns_404_for_unknown_relationship(app_client, session_factory: Any) -> None:
    """Два persons существуют, но Family между ними нет → 404."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    a = await _make_person(session_factory, tree=tree)
    b = await _make_person(session_factory, tree=tree)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{a.id}/{b.id}/evidence",
        headers=_hdr(owner),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_returns_404_for_unknown_person(app_client, session_factory: Any) -> None:
    """Один из person_id не существует в tree → 404."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    a = await _make_person(session_factory, tree=tree)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{a.id}/{uuid.uuid4()}/evidence",
        headers=_hdr(owner),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_returns_403_for_non_member(app_client, session_factory: Any) -> None:
    """User без membership → 403."""
    owner = await _make_user(session_factory)
    intruder = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    a = await _make_person(session_factory, tree=tree)
    b = await _make_person(session_factory, tree=tree)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{a.id}/{b.id}/evidence",
        headers=_hdr(intruder),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_returns_400_for_self_loop(app_client, session_factory: Any) -> None:
    """subject_id == object_id → 400."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    a = await _make_person(session_factory, tree=tree)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/spouse/{a.id}/{a.id}/evidence",
        headers=_hdr(owner),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_unknown_kind_returns_422(app_client, session_factory: Any) -> None:
    """``kind=cousin`` не в enum → 422 от FastAPI validation."""
    owner = await _make_user(session_factory)
    tree = await _make_tree(session_factory, owner=owner)
    a = await _make_person(session_factory, tree=tree)
    b = await _make_person(session_factory, tree=tree)

    resp = await app_client.get(
        f"/trees/{tree.id}/relationships/cousin/{a.id}/{b.id}/evidence",
        headers=_hdr(owner),
    )
    assert resp.status_code == 422
