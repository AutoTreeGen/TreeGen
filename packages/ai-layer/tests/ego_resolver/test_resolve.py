"""Тесты :func:`resolve_reference` (Phase 10.7b).

Acceptance: ≥8 кейсов, покрывающих:

* English direct relationship («my wife», «my mother's brother»)
* Russian Cyrillic («брат матери», «сестра отца», «брат матери жены»)
* Latin-translit («moja zhena»)
* Direct name (одна Dvora → unique; две Olga → ambiguous + alternatives)
* Mixed mode («my wife's mother Olga» → unique через path+name combo)
* Negative («my brother» → ego без братьев → None)
"""

from __future__ import annotations

import uuid

import pytest
from ai_layer.ego_resolver import resolve_reference
from ai_layer.ego_resolver.grammar import parse_reference


def _resolve(fixture: dict[str, object], ref: str) -> object:
    tree = fixture["tree"]
    ego = fixture["ego"]
    return resolve_reference(tree, ego, ref)  # type: ignore[arg-type]


def test_my_wife_resolves_to_spouse(fixture_tree: dict[str, object]) -> None:
    """«my wife» → ego's spouse, confidence=1.0, path=[(spouse, F)]."""
    res = _resolve(fixture_tree, "my wife")
    assert res is not None
    assert res.person_id == fixture_tree["wife"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]
    assert len(res.path) == 1  # type: ignore[attr-defined]
    assert res.path[0].kind == "spouse"  # type: ignore[attr-defined]
    assert res.path[0].sex_hint == "F"  # type: ignore[attr-defined]
    assert res.alternatives == ()  # type: ignore[attr-defined]


def test_my_mother_resolves_to_mother(fixture_tree: dict[str, object]) -> None:
    """«my mother» → ego_mother (Dvora), unique."""
    res = _resolve(fixture_tree, "my mother")
    assert res is not None
    assert res.person_id == fixture_tree["ego_mother"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]


def test_my_mothers_brother_resolves_to_uncle(fixture_tree: dict[str, object]) -> None:
    """«my mother's brother» → uncle (David). 2-hop path."""
    res = _resolve(fixture_tree, "my mother's brother")
    assert res is not None
    assert res.person_id == fixture_tree["uncle"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]
    path = res.path  # type: ignore[attr-defined]
    assert len(path) == 2
    assert path[0].kind == "parent"
    assert path[0].sex_hint == "F"
    assert path[1].kind == "sibling"
    assert path[1].sex_hint == "M"


def test_brat_materi_russian_resolves_to_uncle(fixture_tree: dict[str, object]) -> None:
    """«брат матери» → uncle. Russian (nominative + genitive), reversed."""
    res = _resolve(fixture_tree, "брат матери")
    assert res is not None
    assert res.person_id == fixture_tree["uncle"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]


def test_sestra_ottsa_russian_resolves_to_aunt(fixture_tree: dict[str, object]) -> None:
    """«сестра отца» → aunt (Rachel). Father's sister, Russian."""
    res = _resolve(fixture_tree, "сестра отца")
    assert res is not None
    assert res.person_id == fixture_tree["aunt"]  # type: ignore[attr-defined]


def test_brat_materi_zheny_three_hop(fixture_tree: dict[str, object]) -> None:
    """«брат матери жены» → wife.mother.brother = wife_uncle (Joseph)."""
    res = _resolve(fixture_tree, "брат матери жены")
    assert res is not None
    assert res.person_id == fixture_tree["wife_uncle"]  # type: ignore[attr-defined]
    path = res.path  # type: ignore[attr-defined]
    assert len(path) == 3
    # ego→spouse(F)→parent(F)→sibling(M)
    assert [s.kind for s in path] == ["spouse", "parent", "sibling"]


def test_direct_name_dvora_unique(fixture_tree: dict[str, object]) -> None:
    """«Dvora» → ego_mother (только одна Dvora в дереве). Confidence=1.0 (exact)."""
    res = _resolve(fixture_tree, "Dvora")
    assert res is not None
    assert res.person_id == fixture_tree["ego_mother"]  # type: ignore[attr-defined]
    assert res.confidence == pytest.approx(1.0)  # type: ignore[attr-defined]
    assert res.path == ()  # type: ignore[attr-defined]


def test_translit_moja_zhena_resolves_to_wife(fixture_tree: dict[str, object]) -> None:
    """«moja zhena» (Latin translit) → wife через translit-таблицу."""
    res = _resolve(fixture_tree, "moja zhena")
    assert res is not None
    assert res.person_id == fixture_tree["wife"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]


def test_two_olgas_returns_alternatives(fixture_tree: dict[str, object]) -> None:
    """«Olga» → 2 кандидата (wife + wife_mother). Top + alternatives, confidence < 1.0."""
    res = _resolve(fixture_tree, "Olga")
    assert res is not None
    # Один из двух — top, другой — в alternatives.
    expected_ids: set[uuid.UUID] = {
        fixture_tree["wife"],  # type: ignore[arg-type]
        fixture_tree["wife_mother"],  # type: ignore[arg-type]
    }
    assert res.person_id in expected_ids  # type: ignore[attr-defined]
    assert len(res.alternatives) == 1  # type: ignore[attr-defined]
    assert res.alternatives[0].person_id in expected_ids  # type: ignore[attr-defined]
    assert res.alternatives[0].person_id != res.person_id  # type: ignore[attr-defined]
    # Penalty за ambiguity: confidence < 1.0.
    assert res.confidence < 1.0  # type: ignore[attr-defined]


def test_mixed_my_wifes_mother_olga_uniquely_resolves(
    fixture_tree: dict[str, object],
) -> None:
    """«my wife's mother Olga»: walk → {wife_mother}; name-filter Olga unique."""
    res = _resolve(fixture_tree, "my wife's mother Olga")
    assert res is not None
    assert res.person_id == fixture_tree["wife_mother"]  # type: ignore[attr-defined]
    # Уникальный кандидат после path+name → confidence == 1.0.
    assert res.confidence == pytest.approx(1.0)  # type: ignore[attr-defined]
    assert res.alternatives == ()  # type: ignore[attr-defined]


def test_my_brother_returns_none_when_no_brother(
    fixture_tree: dict[str, object],
) -> None:
    """«my brother» — у ego только sister Sarah → walker filtter sex=M отсекает все → None."""
    res = _resolve(fixture_tree, "my brother")
    assert res is None


def test_my_sister_resolves_uniquely(fixture_tree: dict[str, object]) -> None:
    """«my sister» → sister (Sarah). Walker filter sex=F, single match."""
    res = _resolve(fixture_tree, "my sister")
    assert res is not None
    assert res.person_id == fixture_tree["sister"]  # type: ignore[attr-defined]
    assert res.confidence == 1.0  # type: ignore[attr-defined]


def test_typographic_apostrophe_normalized(fixture_tree: dict[str, object]) -> None:
    """«my wife’s mother» (typographic ’) идентично «my wife's mother» (ASCII ')."""
    res = _resolve(fixture_tree, "my wife’s mother")
    assert res is not None
    assert res.person_id == fixture_tree["wife_mother"]  # type: ignore[attr-defined]


def test_unknown_name_returns_none(fixture_tree: dict[str, object]) -> None:
    """«Иван Петров» — нет такого в дереве → None."""
    res = _resolve(fixture_tree, "Konstantin Pavlovich")
    assert res is None


def test_parse_reference_english_chain_order() -> None:
    """parse_reference: «my mother's brother» → path в порядке ego→target."""
    parsed = parse_reference("my mother's brother")
    assert [s.kind for s in parsed.path] == ["parent", "sibling"]
    assert [s.sex_hint for s in parsed.path] == ["F", "M"]
    assert parsed.name_tail is None


def test_parse_reference_russian_reversed() -> None:
    """parse_reference: «брат матери» → reversed в ego→target order."""
    parsed = parse_reference("брат матери")
    assert [s.kind for s in parsed.path] == ["parent", "sibling"]
    assert [s.sex_hint for s in parsed.path] == ["F", "M"]


def test_parse_reference_pure_name() -> None:
    """parse_reference: «Dvora» — нет kinship-токенов → path пустой, name_tail set."""
    parsed = parse_reference("Dvora")
    assert parsed.path == ()
    assert parsed.name_tail == "dvora"
