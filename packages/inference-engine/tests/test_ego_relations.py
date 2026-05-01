"""Тесты эго-резолвера (Phase 10.7a / ADR-0068).

Critical-test focus (ROADMAP §10.7a, owner's exact pain):

- ``wife.brother`` (degree 2) НЕ должен путаться с ``wife.mother.brother``
  (degree 4 в путях через mother). BFS возвращает разные пути для
  разных person id'ов, kind/degree/via различимы.
- Twin disambiguation: ``wife.twin_brother`` и обычный ``wife.brother``
  имеют одинаковый ``kind``, но различаются ``is_twin``-флагом.
"""

from __future__ import annotations

import uuid

import pytest
from inference_engine.ego_relations import (
    FamilyNode,
    FamilyTraversal,
    NoPathError,
    humanize,
    relate,
)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def four_gen_tree() -> dict[str, object]:
    """4-поколенное дерево: great-grandparents → grandparents → parents
    → ego + spouse + spouse-relatives + siblings (включая twin'а у spouse).

    Structure (M=male, F=female):

    Generation 1 (great-grandparents, paternal side ego):
        ego_paternal_great_grandfather (M) — ego_paternal_great_grandmother (F)
            → ego_paternal_grandfather

    Generation 2:
        ego_paternal_grandfather (M) — ego_paternal_grandmother (F)
            → ego_father
        spouse_maternal_grandfather (M) — spouse_maternal_grandmother (F)
            → spouse_mother (+ spouse_mother_brother как sibling spouse_mother)

    Generation 3:
        ego_father (M) — ego_mother (F) → ego, ego_sister
        spouse_father (M) — spouse_mother (F) → spouse, spouse_brother,
                                                spouse_twin_brother (twin of spouse)

    Generation 4:
        ego (M) — spouse (F) → ego_son
    """
    # Generation 1
    ego_paternal_great_grandfather = _uid()
    ego_paternal_great_grandmother = _uid()
    # Generation 2 (ego side)
    ego_paternal_grandfather = _uid()
    ego_paternal_grandmother = _uid()
    # Generation 2 (spouse side)
    spouse_maternal_grandfather = _uid()
    spouse_maternal_grandmother = _uid()
    # Generation 3 (ego parents, ego siblings, spouse parents, spouse siblings)
    ego_father = _uid()
    ego_mother = _uid()
    spouse_father = _uid()
    spouse_mother = _uid()
    spouse_mother_brother = _uid()  # spouse's mother's brother (wife.mother.brother test)
    # Generation 4 (ego, spouse, ego sibling, spouse siblings + twin)
    ego = _uid()
    ego_sister = _uid()
    spouse = _uid()
    spouse_brother = _uid()
    spouse_twin_brother = _uid()  # twin of spouse
    # Generation 5
    ego_son = _uid()

    # Families
    f_great_grandparents_ego = FamilyNode(
        family_id=_uid(),
        husband_id=ego_paternal_great_grandfather,
        wife_id=ego_paternal_great_grandmother,
        child_ids=(ego_paternal_grandfather,),
    )
    f_grandparents_ego = FamilyNode(
        family_id=_uid(),
        husband_id=ego_paternal_grandfather,
        wife_id=ego_paternal_grandmother,
        child_ids=(ego_father,),
    )
    f_grandparents_spouse = FamilyNode(
        family_id=_uid(),
        husband_id=spouse_maternal_grandfather,
        wife_id=spouse_maternal_grandmother,
        child_ids=(spouse_mother, spouse_mother_brother),
    )
    f_parents_ego = FamilyNode(
        family_id=_uid(),
        husband_id=ego_father,
        wife_id=ego_mother,
        child_ids=(ego, ego_sister),
    )
    f_parents_spouse = FamilyNode(
        family_id=_uid(),
        husband_id=spouse_father,
        wife_id=spouse_mother,
        # spouse + spouse_brother + spouse_twin_brother (last two are twins
        # of each other; spouse is NOT a twin in this fixture).
        child_ids=(spouse, spouse_brother, spouse_twin_brother),
    )
    f_marriage = FamilyNode(
        family_id=_uid(),
        husband_id=ego,
        wife_id=spouse,
        child_ids=(ego_son,),
    )

    families = {
        f.family_id: f
        for f in (
            f_great_grandparents_ego,
            f_grandparents_ego,
            f_grandparents_spouse,
            f_parents_ego,
            f_parents_spouse,
            f_marriage,
        )
    }

    # Indexes
    person_to_parent_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    person_to_spouse_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fam in families.values():
        for child_id in fam.child_ids:
            person_to_parent_families.setdefault(child_id, []).append(fam.family_id)
        for sup in (fam.husband_id, fam.wife_id):
            if sup is not None:
                person_to_spouse_families.setdefault(sup, []).append(fam.family_id)

    person_sex = {
        ego_paternal_great_grandfather: "M",
        ego_paternal_great_grandmother: "F",
        ego_paternal_grandfather: "M",
        ego_paternal_grandmother: "F",
        spouse_maternal_grandfather: "M",
        spouse_maternal_grandmother: "F",
        ego_father: "M",
        ego_mother: "F",
        spouse_father: "M",
        spouse_mother: "F",
        spouse_mother_brother: "M",
        ego: "M",
        ego_sister: "F",
        spouse: "F",
        spouse_brother: "M",
        spouse_twin_brother: "M",
        ego_son: "M",
    }

    # Twin pairs: spouse + spouse_twin_brother are twins of each other.
    # ego→wife (spouse) → twin sibling (spouse_twin_brother) flags is_twin
    # на финальном sibling-ребре (см. test_twin_disambiguation).
    twin_pairs = {frozenset({spouse, spouse_twin_brother})}

    tree = FamilyTraversal(
        families=families,
        person_to_parent_families=person_to_parent_families,
        person_to_spouse_families=person_to_spouse_families,
        person_sex=person_sex,
        twin_pairs=twin_pairs,
    )

    return {
        "tree": tree,
        # endpoints we'll relate against
        "ego": ego,
        "ego_sister": ego_sister,
        "ego_father": ego_father,
        "ego_mother": ego_mother,
        "ego_paternal_grandfather": ego_paternal_grandfather,
        "ego_paternal_great_grandfather": ego_paternal_great_grandfather,
        "ego_son": ego_son,
        "spouse": spouse,
        "spouse_brother": spouse_brother,
        "spouse_twin_brother": spouse_twin_brother,
        "spouse_mother": spouse_mother,
        "spouse_mother_brother": spouse_mother_brother,
    }


def test_self(four_gen_tree: dict[str, object]) -> None:
    """ego == target → kind='self', degree=0."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    path = relate(ego, ego, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "self"
    assert path.degree == 0
    assert path.via == []
    assert path.is_twin is False
    assert path.blood_relation is True


def test_spouse(four_gen_tree: dict[str, object]) -> None:
    """ego → wife: kind='wife', degree=1, blood_relation=False."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    spouse = four_gen_tree["spouse"]
    path = relate(ego, spouse, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "wife"
    assert path.degree == 1
    assert path.via == []
    assert path.is_twin is False
    assert path.blood_relation is False


def test_parent(four_gen_tree: dict[str, object]) -> None:
    """ego → father: kind='father', blood_relation=True."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    ego_father = four_gen_tree["ego_father"]
    path = relate(ego, ego_father, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "father"
    assert path.degree == 1
    assert path.blood_relation is True


def test_grandparent_via_father(four_gen_tree: dict[str, object]) -> None:
    """ego → ego_paternal_grandfather: kind='father.father'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    grandfather = four_gen_tree["ego_paternal_grandfather"]
    path = relate(ego, grandfather, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "father.father"
    assert path.degree == 2
    assert path.blood_relation is True


def test_great_grandparent(four_gen_tree: dict[str, object]) -> None:
    """4-gen depth: kind='father.father.father'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    great = four_gen_tree["ego_paternal_great_grandfather"]
    path = relate(ego, great, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "father.father.father"
    assert path.degree == 3
    assert path.blood_relation is True


def test_sibling(four_gen_tree: dict[str, object]) -> None:
    """ego → ego_sister: kind='sister'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    sister = four_gen_tree["ego_sister"]
    path = relate(ego, sister, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "sister"
    assert path.degree == 1
    assert path.blood_relation is True
    assert path.is_twin is False


def test_child(four_gen_tree: dict[str, object]) -> None:
    """ego → ego_son: kind='son'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    son = four_gen_tree["ego_son"]
    path = relate(ego, son, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "son"
    assert path.degree == 1
    assert path.blood_relation is True


def test_in_law_wife_brother(four_gen_tree: dict[str, object]) -> None:
    """CRITICAL: ego → wife's brother → kind='wife.brother', degree=2.

    Это исходный pain-point владельца — AI'i путали «брата жены» (degree 2)
    с «братом тёщи» (degree 3).
    """
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    spouse = four_gen_tree["spouse"]
    spouse_brother = four_gen_tree["spouse_brother"]
    path = relate(ego, spouse_brother, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "wife.brother"
    assert path.degree == 2
    assert path.via == [spouse]
    assert path.is_twin is False
    assert path.blood_relation is False


def test_in_law_wife_mother_brother(four_gen_tree: dict[str, object]) -> None:
    """CRITICAL: ego → wife's mother's brother → kind='wife.mother.brother', degree=3.

    Pair-test с ``test_in_law_wife_brother``: kind/degree/via различимы;
    BFS не должен дать «wife.brother» для этого target id.
    """
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    spouse = four_gen_tree["spouse"]
    spouse_mother = four_gen_tree["spouse_mother"]
    smb = four_gen_tree["spouse_mother_brother"]
    path = relate(ego, smb, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "wife.mother.brother"
    assert path.degree == 3
    assert path.via == [spouse, spouse_mother]
    assert path.blood_relation is False


def test_critical_wife_brother_vs_wife_mother_brother_different(
    four_gen_tree: dict[str, object],
) -> None:
    """CRITICAL pair: эти два родственника — РАЗНЫЕ target id'ы и
    резолвер даёт разные kind/degree/via. Это инвариант, который ломался
    в pre-10.7a flow (AI «not knowing who you are»).
    """
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    sb_path = relate(ego, four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    smb_path = relate(ego, four_gen_tree["spouse_mother_brother"], tree=tree)  # type: ignore[arg-type]
    assert sb_path.kind != smb_path.kind
    assert sb_path.degree != smb_path.degree
    assert sb_path.via != smb_path.via


def test_twin_disambiguation(four_gen_tree: dict[str, object]) -> None:
    """CRITICAL: twin sibling of spouse: kind='wife.brother' (canonical),
    is_twin=True. Twin'ы разделяются ФЛАГОМ, не отдельным kind'ом — см.
    ADR-0068 §Decision/twin disambiguation.
    """
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    twin = four_gen_tree["spouse_twin_brother"]
    path = relate(ego, twin, tree=tree)  # type: ignore[arg-type]
    assert path.kind == "wife.brother"
    assert path.is_twin is True
    assert path.degree == 2
    assert path.blood_relation is False


def test_twin_humanize_en(four_gen_tree: dict[str, object]) -> None:
    """English humanize: twin вставляется как 'twin brother'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    twin = four_gen_tree["spouse_twin_brother"]
    path = relate(ego, twin, tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "en") == "wife's twin brother"


def test_twin_humanize_ru(four_gen_tree: dict[str, object]) -> None:
    """Русский humanize: 'брат-близнец жены'."""
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    twin = four_gen_tree["spouse_twin_brother"]
    path = relate(ego, twin, tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "ru") == "брат-близнец жены"


def test_humanize_en_wife_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "en") == "wife's brother"


def test_humanize_ru_wife_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "ru") == "брат жены"


def test_humanize_ru_wife_mother_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_mother_brother"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "ru") == "брат матери жены"


def test_humanize_self(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    ego = four_gen_tree["ego"]
    path = relate(ego, ego, tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "en") == "yourself"
    assert humanize(path, "ru") == "вы сами"


def test_humanize_father(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["ego_father"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "en") == "father"
    assert humanize(path, "ru") == "отец"


def test_humanize_de_wife_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    # «Bruder von Ehefrau»: target в номинативе, цепочка von+слово.
    assert humanize(path, "de") == "Bruder von Ehefrau"


def test_humanize_nl_wife_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "nl") == "broer van vrouw"


def test_humanize_he_wife_brother(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    assert humanize(path, "he") == "אח של אישה"


def test_humanize_unsupported_language(four_gen_tree: dict[str, object]) -> None:
    tree = four_gen_tree["tree"]
    path = relate(four_gen_tree["ego"], four_gen_tree["spouse_brother"], tree=tree)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported language"):
        humanize(path, "fr")  # type: ignore[arg-type]


def test_no_path_disconnected() -> None:
    """Disconnected component: NoPathError."""
    a = _uid()
    b = _uid()
    tree = FamilyTraversal(
        families={},
        person_to_parent_families={},
        person_to_spouse_families={},
        person_sex={a: "M", b: "M"},
        twin_pairs=set(),
    )
    with pytest.raises(NoPathError):
        relate(a, b, tree=tree)


def test_blood_preferred_over_in_law_at_same_length() -> None:
    """Tiebreaker: при равной длине пути BFS предпочитает blood-relation
    путь in-law'у (см. ADR-0068 §Decision/preference).

    Setup: A — отец B; B и C — спутники брака; A — отец C тоже (необычно,
    но допустимо для теста tiebreaker'а: A → C можно достичь через child
    (blood, 1 hop) ИЛИ через child + spouse (2 hops); 1-hop blood выигрывает
    очевидно. Делаем равно-длинный конкурс: A → B (child, blood, 1 hop) vs
    A → spouse_of_B (через child + spouse, 2 hops). Чтобы сделать tie, нужны
    два пути в 2 hops: один все blood, другой со spouse.

    Tree:
        ego — папа двух детей (sister1, sister2)
        sister1 замужем за husband_x
        sister2 замужем за husband_x  ← намеренно тот же человек

    ego → husband_x:
        path A: ego → sister1 (child) → husband_x (spouse) — 2 hops, 1 spouse
        path B: ego → sister2 (child) → husband_x (spouse) — 2 hops, 1 spouse
    Это не интересный tie (оба in-law). Сделаем по-другому: husband_x =
    биологический сын ego И одновременно муж sister1 (incestuous, но для
    тестового invariant'а покажет, что blood path выигрывает).
    """
    ego = _uid()
    spouse_ego = _uid()
    daughter = _uid()
    son_aka_husband = _uid()  # сразу сын ego И муж daughter

    f_marriage_ego = FamilyNode(
        family_id=_uid(),
        husband_id=ego,
        wife_id=spouse_ego,
        child_ids=(daughter, son_aka_husband),
    )
    f_daughter_marriage = FamilyNode(
        family_id=_uid(),
        husband_id=son_aka_husband,
        wife_id=daughter,
        child_ids=(),
    )

    families = {
        f_marriage_ego.family_id: f_marriage_ego,
        f_daughter_marriage.family_id: f_daughter_marriage,
    }
    person_to_parent_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    person_to_spouse_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fam in families.values():
        for child_id in fam.child_ids:
            person_to_parent_families.setdefault(child_id, []).append(fam.family_id)
        for sup in (fam.husband_id, fam.wife_id):
            if sup is not None:
                person_to_spouse_families.setdefault(sup, []).append(fam.family_id)

    tree = FamilyTraversal(
        families=families,
        person_to_parent_families=person_to_parent_families,
        person_to_spouse_families=person_to_spouse_families,
        person_sex={ego: "M", spouse_ego: "F", daughter: "F", son_aka_husband: "M"},
        twin_pairs=set(),
    )

    # ego → son_aka_husband: blood-path (child, 1 hop) выигрывает.
    path = relate(ego, son_aka_husband, tree=tree)
    assert path.kind == "son"
    assert path.degree == 1
    assert path.blood_relation is True
