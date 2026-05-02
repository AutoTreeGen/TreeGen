"""Phase 5.8 — unit tests, one per validator rule.

Each rule is exercised in isolation via ``validate_document(doc, rules=[rule])``
so cross-rule interaction never confuses assertions. The conftest ``make_*``
helpers keep each test compact.
"""

from __future__ import annotations

from gedcom_parser.models import GedcomRecord
from gedcom_parser.validator import Severity, ValidatorContext, validate_document
from gedcom_parser.validator.rules.broken_xref import BrokenCrossRefRule
from gedcom_parser.validator.rules.duplicate_child import DuplicateChildRule
from gedcom_parser.validator.rules.duplicate_spouse import DuplicateSpouseRule
from gedcom_parser.validator.rules.geography import GeographyImpossibilityRule
from gedcom_parser.validator.rules.missing_xref import MissingXrefRule
from gedcom_parser.validator.rules.parent_age import (
    FatherAgeAtChildBirthRule,
    MotherAgeAtChildBirthRule,
)
from gedcom_parser.validator.rules.parent_alive import ChildBirthAfterParentDeathRule
from gedcom_parser.validator.rules.same_sex_spouse import SameSexSpousePairRule
from gedcom_parser.validator.rules.self_consistency import DeathBeforeBirthRule

from .conftest import make_doc, make_family, make_person

# -----------------------------------------------------------------------------
# Mother age rule
# -----------------------------------------------------------------------------


def test_mother_age_low_warning() -> None:
    """Mother born 12 years before child → WARNING."""
    mother = make_person("I1", sex="F", birth_year=1900)
    child = make_person("I2", birth_year=1912)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[MotherAgeAtChildBirthRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "mother_age_low"
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["age_at_birth_years"] == 12


def test_mother_age_high_error() -> None:
    """Mother 60 at child birth → ERROR."""
    mother = make_person("I1", sex="F", birth_year=1900)
    child = make_person("I2", birth_year=1960)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[MotherAgeAtChildBirthRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "mother_age_high"
    assert findings[0].severity is Severity.ERROR


def test_mother_age_normal_no_finding() -> None:
    """Mother age 30 → no finding."""
    mother = make_person("I1", sex="F", birth_year=1900)
    child = make_person("I2", birth_year=1930)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[MotherAgeAtChildBirthRule()])
    assert findings == []


def test_mother_age_skips_when_birth_year_missing() -> None:
    """Missing year on either side → silent skip, no finding."""
    mother = make_person("I1", sex="F")  # no birth year
    child = make_person("I2", birth_year=1930)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[MotherAgeAtChildBirthRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Father age rule
# -----------------------------------------------------------------------------


def test_father_age_high_error() -> None:
    """Father 80 at child birth → ERROR."""
    father = make_person("I1", sex="M", birth_year=1900)
    child = make_person("I2", birth_year=1980)
    fam = make_family("F1", husband_xref="I1", children_xrefs=("I2",))
    doc = make_doc([father, child], [fam])
    findings = validate_document(doc, rules=[FatherAgeAtChildBirthRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "father_age_high"
    assert findings[0].severity is Severity.ERROR


def test_father_age_low_warning() -> None:
    """Father age 13 → WARNING."""
    father = make_person("I1", sex="M", birth_year=1900)
    child = make_person("I2", birth_year=1913)
    fam = make_family("F1", husband_xref="I1", children_xrefs=("I2",))
    doc = make_doc([father, child], [fam])
    findings = validate_document(doc, rules=[FatherAgeAtChildBirthRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "father_age_low"
    assert findings[0].severity is Severity.WARNING


# -----------------------------------------------------------------------------
# Child birth after parent death (month-precision)
# -----------------------------------------------------------------------------


def test_child_after_mother_death_month_precision() -> None:
    """Child born after mother's death (month precision both) → ERROR."""
    mother = make_person("I1", sex="F", birth_year=1900, death_year=1920, death_month=6)
    child = make_person("I2", birth_year=1921, birth_month=3)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[ChildBirthAfterParentDeathRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "child_born_after_mother_death"


def test_child_after_father_death_within_grace_no_finding() -> None:
    """Child born 5 months after father's death — within posthumous grace, no finding."""
    father = make_person(
        "I1", sex="M", birth_year=1900, death_year=1920, death_month=1, death_day=15
    )
    child = make_person("I2", birth_year=1920, birth_month=6, birth_day=1)
    fam = make_family("F1", husband_xref="I1", children_xrefs=("I2",))
    doc = make_doc([father, child], [fam])
    findings = validate_document(doc, rules=[ChildBirthAfterParentDeathRule()])
    assert findings == []


def test_child_after_father_death_long_after_error() -> None:
    """Child born 2 years after father's death → ERROR."""
    father = make_person(
        "I1", sex="M", birth_year=1900, death_year=1920, death_month=1, death_day=15
    )
    child = make_person("I2", birth_year=1922, birth_month=6, birth_day=1)
    fam = make_family("F1", husband_xref="I1", children_xrefs=("I2",))
    doc = make_doc([father, child], [fam])
    findings = validate_document(doc, rules=[ChildBirthAfterParentDeathRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "child_born_long_after_father_death"


def test_child_after_parent_death_skips_year_only() -> None:
    """Year-only dates → rule skips silently (insufficient precision)."""
    mother = make_person("I1", sex="F", birth_year=1900, death_year=1920)
    child = make_person("I2", birth_year=1921)
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2",))
    doc = make_doc([mother, child], [fam])
    findings = validate_document(doc, rules=[ChildBirthAfterParentDeathRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Death before birth
# -----------------------------------------------------------------------------


def test_death_before_birth_error() -> None:
    """Death year before birth year → ERROR."""
    p = make_person("I1", birth_year=1900, death_year=1850)
    doc = make_doc([p])
    findings = validate_document(doc, rules=[DeathBeforeBirthRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "death_before_birth"
    assert findings[0].severity is Severity.ERROR


def test_death_after_birth_no_finding() -> None:
    """Normal lifespan → no finding."""
    p = make_person("I1", birth_year=1900, death_year=1980)
    doc = make_doc([p])
    findings = validate_document(doc, rules=[DeathBeforeBirthRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Same-sex spouse pair
# -----------------------------------------------------------------------------


def test_same_sex_spouse_pair_warning() -> None:
    """Both sex=M → WARNING."""
    h = make_person("I1", sex="M")
    w = make_person("I2", sex="M")
    fam = make_family("F1", husband_xref="I1", wife_xref="I2")
    doc = make_doc([h, w], [fam])
    findings = validate_document(doc, rules=[SameSexSpousePairRule()])
    assert len(findings) == 1
    assert findings[0].severity is Severity.WARNING
    assert findings[0].context["shared_sex"] == "M"


def test_same_sex_spouse_skipped_when_unknown_sex() -> None:
    """Sex=U → no finding (insufficient information)."""
    h = make_person("I1", sex="U")
    w = make_person("I2", sex="M")
    fam = make_family("F1", husband_xref="I1", wife_xref="I2")
    doc = make_doc([h, w], [fam])
    findings = validate_document(doc, rules=[SameSexSpousePairRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Duplicate spouse
# -----------------------------------------------------------------------------


def test_duplicate_spouse_error() -> None:
    """Same xref in HUSB and WIFE → ERROR."""
    p = make_person("I1", sex="M")
    fam = make_family("F1", husband_xref="I1", wife_xref="I1")
    doc = make_doc([p], [fam])
    findings = validate_document(doc, rules=[DuplicateSpouseRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "duplicate_spouse"
    assert findings[0].severity is Severity.ERROR


# -----------------------------------------------------------------------------
# Duplicate child
# -----------------------------------------------------------------------------


def test_duplicate_child_error() -> None:
    """Same person twice in CHIL → ERROR with occurrences=2."""
    parent = make_person("I1", sex="F")
    child = make_person("I2")
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2", "I2"))
    doc = make_doc([parent, child], [fam])
    findings = validate_document(doc, rules=[DuplicateChildRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "duplicate_child"
    assert findings[0].context["occurrences"] == 2


def test_no_duplicate_child_no_finding() -> None:
    """Distinct children → no finding."""
    parent = make_person("I1", sex="F")
    a = make_person("I2")
    b = make_person("I3")
    fam = make_family("F1", wife_xref="I1", children_xrefs=("I2", "I3"))
    doc = make_doc([parent, a, b], [fam])
    findings = validate_document(doc, rules=[DuplicateChildRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Geography (stub)
# -----------------------------------------------------------------------------


def test_geography_stub_returns_no_findings() -> None:
    """V1 stub always returns []."""
    p = make_person("I1")
    doc = make_doc([p])
    findings = validate_document(doc, rules=[GeographyImpossibilityRule()])
    assert findings == []


# -----------------------------------------------------------------------------
# Broken cross-ref
# -----------------------------------------------------------------------------


def test_broken_fams_xref_is_error() -> None:
    """Person.FAMS pointing to a non-existent family → ERROR (structural)."""
    p = make_person("I1", families_as_spouse=("F-MISSING",))
    doc = make_doc([p])
    findings = validate_document(doc, rules=[BrokenCrossRefRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "broken_xref_fams"
    assert findings[0].severity is Severity.ERROR


def test_broken_husb_xref_is_error() -> None:
    """Family.HUSB pointing to non-existent person → ERROR (structural)."""
    fam = make_family("F1", husband_xref="I-MISSING")
    doc = make_doc([], [fam])
    findings = validate_document(doc, rules=[BrokenCrossRefRule()])
    assert len(findings) == 1
    assert findings[0].rule_id == "broken_xref_husb"


# -----------------------------------------------------------------------------
# Missing xref (uses ctx.raw_records)
# -----------------------------------------------------------------------------


def test_missing_xref_finds_xref_less_indi() -> None:
    """Top-level INDI with no xref → ERROR finding (via ctx.raw_records)."""
    bad_indi = GedcomRecord(level=0, tag="INDI", value="", line_no=42)
    ctx = ValidatorContext(raw_records=(bad_indi,))
    doc = make_doc([])
    findings = validate_document(doc, rules=[MissingXrefRule()], ctx=ctx)
    assert len(findings) == 1
    assert findings[0].rule_id == "missing_xref"
    assert findings[0].context["tag"] == "INDI"
    assert findings[0].context["line_no"] == 42


def test_missing_xref_no_op_without_ctx() -> None:
    """Without raw_records, rule silently no-ops."""
    doc = make_doc([])
    findings = validate_document(doc, rules=[MissingXrefRule()])
    assert findings == []
