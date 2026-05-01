"""Fixture-дерево для ego_resolver-тестов.

Структура (M/F = пол):

    Generation 1 (grandparents-ego maternal): mikhail (M) + rivka (F)
    Generation 1 (grandparents-ego paternal): isaac (M) + leah (F)
    Generation 1 (grandparents-wife maternal): boris_sr (M) + tatiana (F)
    Generation 2:
        ego_father=Avraham (M) + ego_mother=Dvora (F) → ego, sister=Sarah
        uncle=David (M) — sibling of Dvora (через Mikhail+Rivka)
        aunt=Rachel (F) — sibling of Avraham (через Isaac+Leah)
        wife_mother=Olga Smith (F) — second Olga в дереве
        wife_father=Boris Smith (M)
        wife_uncle=Joseph (M) — sibling of Olga Smith (для wife.mother.brother)
    Generation 3:
        ego (M)=Vladimir Levin + wife (F)=Olga Cohen — без детей в фикстуре

Тестируемые reference'ы:

* «my wife» → wife (Olga Cohen)
* «my mother» → ego_mother (Dvora)
* «my mother's brother» → uncle (David)
* «брат матери» → uncle (RU)
* «сестра отца» → aunt (Rachel) (RU)
* «брат матери жены» → wife_uncle (Joseph) (RU, 3-step)
* «Dvora» → ego_mother (unique name)
* «Olga» → ambiguous (wife + wife_mother), alternatives populated
* «my wife's mother Olga» → wife_mother (mixed-mode unique)
* «moja zhena» → wife (translit)
* «my brother» → None (нет brother'а у ego)
"""

from __future__ import annotations

import uuid

import pytest
from ai_layer.ego_resolver import PersonNames, TreeContext
from inference_engine.ego_relations import FamilyNode, FamilyTraversal


def _uid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def fixture_tree() -> dict[str, object]:
    """Базовое 3-поколенное дерево с двумя Олгами и uncle/aunt линиями."""
    # Generation 1 — grandparents (silent UUIDs, нужны только для sibling relations)
    gf_ego_maternal = _uid()  # Mikhail (Dvora's father)
    gm_ego_maternal = _uid()  # Rivka (Dvora's mother)
    gf_ego_paternal = _uid()  # Isaac (Avraham's father)
    gm_ego_paternal = _uid()  # Leah (Avraham's mother)
    gf_wife_maternal = _uid()  # Boris Sr (Olga Smith's father)
    gm_wife_maternal = _uid()  # Tatiana (Olga Smith's mother)

    # Generation 2 — ego's parents, ego's mother's brother (uncle), ego's father's sister (aunt),
    # wife's parents, wife's mother's brother
    ego_mother = _uid()  # Dvora Levin
    ego_father = _uid()  # Avraham Levin
    uncle = _uid()  # David Cohen (Dvora's brother)
    aunt = _uid()  # Rachel Levin (Avraham's sister)
    wife_mother = _uid()  # Olga Smith — second Olga
    wife_father = _uid()  # Boris Smith
    wife_uncle = _uid()  # Joseph Smith (Olga Smith's brother)

    # Generation 3 — ego, ego's sister, wife
    ego = _uid()  # Vladimir Levin
    sister = _uid()  # Sarah Levin
    wife = _uid()  # Olga Cohen

    # Families
    f_ego_maternal_grandparents = FamilyNode(
        family_id=_uid(),
        husband_id=gf_ego_maternal,
        wife_id=gm_ego_maternal,
        child_ids=(ego_mother, uncle),
    )
    f_ego_paternal_grandparents = FamilyNode(
        family_id=_uid(),
        husband_id=gf_ego_paternal,
        wife_id=gm_ego_paternal,
        child_ids=(ego_father, aunt),
    )
    f_ego_parents = FamilyNode(
        family_id=_uid(),
        husband_id=ego_father,
        wife_id=ego_mother,
        child_ids=(ego, sister),
    )
    f_wife_grandparents = FamilyNode(
        family_id=_uid(),
        husband_id=gf_wife_maternal,
        wife_id=gm_wife_maternal,
        child_ids=(wife_mother, wife_uncle),
    )
    f_wife_parents = FamilyNode(
        family_id=_uid(),
        husband_id=wife_father,
        wife_id=wife_mother,
        child_ids=(wife,),
    )
    f_marriage = FamilyNode(
        family_id=_uid(),
        husband_id=ego,
        wife_id=wife,
        child_ids=(),
    )

    families = {
        f.family_id: f
        for f in (
            f_ego_maternal_grandparents,
            f_ego_paternal_grandparents,
            f_ego_parents,
            f_wife_grandparents,
            f_wife_parents,
            f_marriage,
        )
    }

    person_to_parent_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    person_to_spouse_families: dict[uuid.UUID, list[uuid.UUID]] = {}
    for fam in families.values():
        for child_id in fam.child_ids:
            person_to_parent_families.setdefault(child_id, []).append(fam.family_id)
        for sup in (fam.husband_id, fam.wife_id):
            if sup is not None:
                person_to_spouse_families.setdefault(sup, []).append(fam.family_id)

    person_sex = {
        gf_ego_maternal: "M",
        gm_ego_maternal: "F",
        gf_ego_paternal: "M",
        gm_ego_paternal: "F",
        gf_wife_maternal: "M",
        gm_wife_maternal: "F",
        ego_mother: "F",
        ego_father: "M",
        uncle: "M",
        aunt: "F",
        wife_mother: "F",
        wife_father: "M",
        wife_uncle: "M",
        ego: "M",
        sister: "F",
        wife: "F",
    }

    traversal = FamilyTraversal(
        families=families,
        person_to_parent_families=person_to_parent_families,
        person_to_spouse_families=person_to_spouse_families,
        person_sex=person_sex,
        twin_pairs=set(),
    )

    persons = {
        ego: PersonNames(person_id=ego, given="Vladimir", surname="Levin"),
        ego_mother: PersonNames(
            person_id=ego_mother,
            given="Dvora",
            surname="Levin",
            aliases=("Дворa", "Двора Левина"),
        ),
        ego_father: PersonNames(person_id=ego_father, given="Avraham", surname="Levin"),
        uncle: PersonNames(person_id=uncle, given="David", surname="Cohen"),
        aunt: PersonNames(person_id=aunt, given="Rachel", surname="Levin"),
        sister: PersonNames(person_id=sister, given="Sarah", surname="Levin"),
        wife: PersonNames(
            person_id=wife,
            given="Olga",
            surname="Cohen",
            aliases=("Ольга",),
        ),
        wife_mother: PersonNames(
            person_id=wife_mother,
            given="Olga",
            surname="Smith",
        ),
        wife_father: PersonNames(person_id=wife_father, given="Boris", surname="Smith"),
        wife_uncle: PersonNames(person_id=wife_uncle, given="Joseph", surname="Smith"),
    }

    tree = TreeContext(traversal=traversal, persons=persons)

    return {
        "tree": tree,
        "ego": ego,
        "wife": wife,
        "ego_mother": ego_mother,
        "ego_father": ego_father,
        "uncle": uncle,
        "aunt": aunt,
        "sister": sister,
        "wife_mother": wife_mother,
        "wife_father": wife_father,
        "wife_uncle": wife_uncle,
    }
