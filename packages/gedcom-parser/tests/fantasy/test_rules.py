"""Unit tests для каждого fantasy rule (Phase 5.10).

Per-rule под минимальным synthetic doc'ом. Не использует БД, не использует
parse_file — чистая Python.
"""

from __future__ import annotations

import copy

from gedcom_parser.fantasy import scan_document
from gedcom_parser.fantasy.rules.date_impossibility import (
    BirthAfterDeathRule,
    ImpossibleLifespanRule,
)
from gedcom_parser.fantasy.rules.descent_chain import (
    SuspiciousGenerationalCompressionRule,
)
from gedcom_parser.fantasy.rules.parent_age import (
    ParentTooOldAtBirthRule,
    ParentTooYoungAtBirthRule,
)
from gedcom_parser.fantasy.rules.parent_alive import (
    DeathBeforeChildBirthFatherRule,
    DeathBeforeChildBirthMotherRule,
)
from gedcom_parser.fantasy.rules.parent_order import ChildBeforeParentBirthRule
from gedcom_parser.fantasy.rules.structural import (
    CircularDescentRule,
    IdenticalBirthYearSiblingsExcessRule,
    MassFabricatedBranchRule,
)
from gedcom_parser.fantasy.types import FantasySeverity

from .conftest import make_doc, make_family, make_person

# ── impossible_lifespan ──────────────────────────────────────────────────────


def test_impossible_lifespan_under_threshold_no_flag() -> None:
    """span < 122 → no flag."""
    doc = make_doc([make_person("I1", birth_year=1850, death_year=1920)])
    flags = scan_document(doc, rules=[ImpossibleLifespanRule()])
    assert flags == []


def test_impossible_lifespan_edge_122_no_flag() -> None:
    """span == 122 (Calment limit) → no flag."""
    doc = make_doc([make_person("I1", birth_year=1875, death_year=1997)])
    flags = scan_document(doc, rules=[ImpossibleLifespanRule()])
    assert flags == []


def test_impossible_lifespan_high_severity() -> None:
    """123-130 → HIGH."""
    doc = make_doc([make_person("I1", birth_year=1850, death_year=1975)])
    flags = scan_document(doc, rules=[ImpossibleLifespanRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.HIGH
    assert flags[0].evidence["span_years"] == 125


def test_impossible_lifespan_critical_above_130() -> None:
    """span > 130 → CRITICAL."""
    doc = make_doc([make_person("I1", birth_year=1800, death_year=1950)])
    flags = scan_document(doc, rules=[ImpossibleLifespanRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.CRITICAL


# ── birth_after_death ────────────────────────────────────────────────────────


def test_birth_after_death_critical() -> None:
    """birth > death — CRITICAL."""
    doc = make_doc([make_person("I1", birth_year=1900, death_year=1850)])
    flags = scan_document(doc, rules=[BirthAfterDeathRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.CRITICAL
    assert flags[0].confidence <= 0.95
    assert flags[0].person_xref == "I1"


def test_birth_after_death_no_flag_when_normal() -> None:
    doc = make_doc([make_person("I1", birth_year=1850, death_year=1900)])
    flags = scan_document(doc, rules=[BirthAfterDeathRule()])
    assert flags == []


# ── child_before_parent_birth ────────────────────────────────────────────────


def test_child_before_parent_birth() -> None:
    mother = make_person("I_M", sex="F", birth_year=1900)
    child = make_person("I_C", birth_year=1850)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[ChildBeforeParentBirthRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.CRITICAL
    assert flags[0].evidence["role"] == "mother"


def test_child_after_parent_no_flag() -> None:
    father = make_person("I_F", sex="M", birth_year=1830)
    child = make_person("I_C", birth_year=1860)
    fam = make_family("F1", husband_xref="I_F", children_xrefs=("I_C",))
    doc = make_doc([father, child], [fam])
    flags = scan_document(doc, rules=[ChildBeforeParentBirthRule()])
    assert flags == []


# ── parent_too_young_at_birth (4 cases per parent) ───────────────────────────


def test_parent_too_young_under_threshold() -> None:
    """age 5 → flag HIGH."""
    mother = make_person("I_M", sex="F", birth_year=1900)
    child = make_person("I_C", birth_year=1905)  # mother age 5
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[ParentTooYoungAtBirthRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.HIGH
    assert flags[0].evidence["age_at_birth"] == 5


def test_parent_too_young_lower_edge_8_flags() -> None:
    """age 8 (still < 9) → flag."""
    father = make_person("I_F", sex="M", birth_year=1900)
    child = make_person("I_C", birth_year=1908)
    fam = make_family("F1", husband_xref="I_F", children_xrefs=("I_C",))
    doc = make_doc([father, child], [fam])
    flags = scan_document(doc, rules=[ParentTooYoungAtBirthRule()])
    assert len(flags) == 1


def test_parent_too_young_at_threshold_9_no_flag() -> None:
    """age == 9 (== threshold) → no flag (rule is `< 9`)."""
    mother = make_person("I_M", sex="F", birth_year=1900)
    child = make_person("I_C", birth_year=1909)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[ParentTooYoungAtBirthRule()])
    assert flags == []


def test_parent_too_young_above_threshold_no_flag() -> None:
    """age 25 → no flag."""
    mother = make_person("I_M", sex="F", birth_year=1900)
    child = make_person("I_C", birth_year=1925)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[ParentTooYoungAtBirthRule()])
    assert flags == []


# ── parent_too_old_at_birth (mother + father variants) ───────────────────────


def test_mother_too_old_warning() -> None:
    """mother age 60 (>55) → WARNING."""
    mother = make_person("I_M", sex="F", birth_year=1900)
    child = make_person("I_C", birth_year=1960)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[ParentTooOldAtBirthRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.WARNING
    assert flags[0].evidence["role"] == "mother"


def test_father_too_old_warning() -> None:
    """father age 85 (>80) → WARNING."""
    father = make_person("I_F", sex="M", birth_year=1900)
    child = make_person("I_C", birth_year=1985)
    fam = make_family("F1", husband_xref="I_F", children_xrefs=("I_C",))
    doc = make_doc([father, child], [fam])
    flags = scan_document(doc, rules=[ParentTooOldAtBirthRule()])
    assert len(flags) == 1
    assert flags[0].evidence["role"] == "father"


# ── death_before_child_birth (mother + father) ───────────────────────────────


def test_mother_died_before_child_critical() -> None:
    mother = make_person("I_M", sex="F", birth_year=1850, death_year=1880)
    child = make_person("I_C", birth_year=1881)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[DeathBeforeChildBirthMotherRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.CRITICAL


def test_mother_died_same_year_no_flag_year_precision() -> None:
    """child_b == mother_d → no flag (could be giving birth then dying)."""
    mother = make_person("I_M", sex="F", birth_year=1850, death_year=1880)
    child = make_person("I_C", birth_year=1880)
    fam = make_family("F1", wife_xref="I_M", children_xrefs=("I_C",))
    doc = make_doc([mother, child], [fam])
    flags = scan_document(doc, rules=[DeathBeforeChildBirthMotherRule()])
    assert flags == []


def test_father_died_within_grace_no_flag() -> None:
    """father_d=1880, child_b=1881 → posthumous birth grace → no flag."""
    father = make_person("I_F", sex="M", birth_year=1850, death_year=1880)
    child = make_person("I_C", birth_year=1881)
    fam = make_family("F1", husband_xref="I_F", children_xrefs=("I_C",))
    doc = make_doc([father, child], [fam])
    flags = scan_document(doc, rules=[DeathBeforeChildBirthFatherRule()])
    assert flags == []


def test_father_died_beyond_grace_critical() -> None:
    """father_d=1880, child_b=1883 → 3y gap > 1y grace → CRITICAL."""
    father = make_person("I_F", sex="M", birth_year=1850, death_year=1880)
    child = make_person("I_C", birth_year=1883)
    fam = make_family("F1", husband_xref="I_F", children_xrefs=("I_C",))
    doc = make_doc([father, child], [fam])
    flags = scan_document(doc, rules=[DeathBeforeChildBirthFatherRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.CRITICAL


# ── circular_descent ─────────────────────────────────────────────────────────


def test_circular_descent_3_cycle() -> None:
    """A is parent of B, B parent of C, C parent of A → cycle."""
    a = make_person("I_A")
    b = make_person("I_B")
    c = make_person("I_C")
    f1 = make_family("F1", husband_xref="I_A", children_xrefs=("I_B",))
    f2 = make_family("F2", husband_xref="I_B", children_xrefs=("I_C",))
    f3 = make_family("F3", husband_xref="I_C", children_xrefs=("I_A",))
    doc = make_doc([a, b, c], [f1, f2, f3])
    flags = scan_document(doc, rules=[CircularDescentRule()])
    assert len(flags) >= 1
    assert flags[0].severity is FantasySeverity.CRITICAL
    assert set(flags[0].evidence["cycle_xrefs"]) == {"I_A", "I_B", "I_C"}


def test_circular_descent_4_cycle() -> None:
    a = make_person("I_A")
    b = make_person("I_B")
    c = make_person("I_C")
    d = make_person("I_D")
    f1 = make_family("F1", husband_xref="I_A", children_xrefs=("I_B",))
    f2 = make_family("F2", husband_xref="I_B", children_xrefs=("I_C",))
    f3 = make_family("F3", husband_xref="I_C", children_xrefs=("I_D",))
    f4 = make_family("F4", husband_xref="I_D", children_xrefs=("I_A",))
    doc = make_doc([a, b, c, d], [f1, f2, f3, f4])
    flags = scan_document(doc, rules=[CircularDescentRule()])
    assert any(set(f.evidence["cycle_xrefs"]) == {"I_A", "I_B", "I_C", "I_D"} for f in flags)


def test_circular_descent_no_cycle() -> None:
    a = make_person("I_A")
    b = make_person("I_B")
    f1 = make_family("F1", husband_xref="I_A", children_xrefs=("I_B",))
    doc = make_doc([a, b], [f1])
    flags = scan_document(doc, rules=[CircularDescentRule()])
    assert flags == []


# ── identical_birth_year_siblings_excess ────────────────────────────────────


def test_3_siblings_same_year_no_flag() -> None:
    """Triplets — within threshold."""
    triplets = [make_person(f"I_C{i}", birth_year=1900) for i in range(3)]
    fam = make_family("F1", children_xrefs=tuple(p.xref_id for p in triplets))
    doc = make_doc(triplets, [fam])
    flags = scan_document(doc, rules=[IdenticalBirthYearSiblingsExcessRule()])
    assert flags == []


def test_4_siblings_same_year_warning() -> None:
    quads = [make_person(f"I_C{i}", birth_year=1900) for i in range(4)]
    fam = make_family("F1", children_xrefs=tuple(p.xref_id for p in quads))
    doc = make_doc(quads, [fam])
    flags = scan_document(doc, rules=[IdenticalBirthYearSiblingsExcessRule()])
    assert len(flags) == 1
    assert flags[0].severity is FantasySeverity.WARNING


# ── mass_fabricated_branch ──────────────────────────────────────────────────


def test_mass_fabricated_25_persons_one_year_no_citations() -> None:
    """25 connected persons, all born 1850, zero citations → HIGH."""
    persons = [make_person(f"I{i:03d}", birth_year=1850) for i in range(25)]
    # Wire in a single chain through 24 marriages so connected.
    families = []
    for i in range(24):
        families.append(
            make_family(
                f"F{i:03d}",
                husband_xref=f"I{i:03d}",
                children_xrefs=(f"I{i + 1:03d}",),
            )
        )
    doc = make_doc(persons, families)
    flags = scan_document(doc, rules=[MassFabricatedBranchRule()])
    assert any(f.rule_id == "mass_fabricated_branch" for f in flags)


def test_mass_fabricated_branch_under_size_no_flag() -> None:
    persons = [make_person(f"I{i:02d}", birth_year=1850) for i in range(5)]
    families = [
        make_family(f"F{i:02d}", husband_xref=f"I{i:02d}", children_xrefs=(f"I{i + 1:02d}",))
        for i in range(4)
    ]
    doc = make_doc(persons, families)
    flags = scan_document(doc, rules=[MassFabricatedBranchRule()])
    assert flags == []


# ── suspicious_generational_compression ─────────────────────────────────────


def test_suspicious_compression_5_gens_avg_10_years() -> None:
    """5 поколений по 10 лет каждое → HIGH."""
    persons = [make_person(f"I{i}", birth_year=1900 - i * 10) for i in range(5)]
    families = []
    for i in range(4):
        # parent = persons[i+1], child = persons[i]
        families.append(make_family(f"F{i}", wife_xref=f"I{i + 1}", children_xrefs=(f"I{i}",)))
    doc = make_doc(persons, families)
    flags = scan_document(doc, rules=[SuspiciousGenerationalCompressionRule()])
    assert any(f.rule_id == "suspicious_generational_compression" for f in flags)
    flag = next(f for f in flags if f.rule_id == "suspicious_generational_compression")
    assert flag.severity is FantasySeverity.HIGH
    assert flag.evidence["avg_gap_years"] == 10.0


# ── full_scan + dismiss/severity-filter behaviour ───────────────────────────


def test_full_scan_runs_all_default_rules() -> None:
    """Empty doc — no flags but no exceptions."""
    doc = make_doc()
    flags = scan_document(doc)
    assert flags == []


def test_severity_filter_via_enabled_rules() -> None:
    """ctx.enabled_rules whitelisting ограничивает scan."""
    from gedcom_parser.fantasy.types import FantasyContext

    doc = make_doc([make_person("I1", birth_year=1900, death_year=1850)])
    flags = scan_document(
        doc,
        ctx=FantasyContext(enabled_rules=frozenset({"impossible_lifespan"})),
    )
    # birth_after_death НЕ в whitelist → не сработало.
    assert all(f.rule_id == "impossible_lifespan" for f in flags)


# ── no-mutation invariant ────────────────────────────────────────────────────


def test_no_mutation_invariant() -> None:
    """scan не мутирует ни doc, ни persons / families.

    Делаем deep-copy до scan'а; после — проверяем equality (pydantic frozen
    модели сравниваются by-value).
    """
    persons = [
        make_person("I1", birth_year=1900, death_year=1850),  # birth_after_death
        make_person("I2", birth_year=1850, death_year=1880),  # mother
        make_person("I3", birth_year=1900),  # child too young parent
    ]
    families = [make_family("F1", wife_xref="I2", children_xrefs=("I3",))]
    doc = make_doc(persons, families)
    # Snapshot до scan'а.
    snap_persons = copy.deepcopy(doc.persons)
    snap_families = copy.deepcopy(doc.families)
    _flags = scan_document(doc)
    # Doc и его entities не должны быть тронуты.
    assert doc.persons == snap_persons
    assert doc.families == snap_families
