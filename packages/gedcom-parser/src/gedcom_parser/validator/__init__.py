"""GEDCOM validator (Phase 5.8) — rule-based linter producing structured findings.

Public surface:

* :func:`validate_document` — run all (or a subset of) rules against a
  :class:`gedcom_parser.document.GedcomDocument` and collect findings.
* :class:`Finding` / :class:`Severity` — structured output type.
* :class:`ValidatorContext` — optional inputs for rules that need raw
  records (e.g. :class:`MissingXrefRule`).
* :class:`ValidatorRule` — Protocol that any rule must satisfy.
* :func:`default_rules` — list of all built-in rules.

Example::

    from gedcom_parser import parse_document_file
    from gedcom_parser.validator import validate_document

    doc = parse_document_file("tree.ged")
    findings = validate_document(doc)
    for f in findings:
        print(f.severity.value, f.rule_id, f.message)
"""

from __future__ import annotations

from gedcom_parser.validator.engine import ValidatorRule, validate_document
from gedcom_parser.validator.rules import default_rules
from gedcom_parser.validator.types import Finding, Severity, ValidatorContext

__all__ = [
    "Finding",
    "Severity",
    "ValidatorContext",
    "ValidatorRule",
    "default_rules",
    "validate_document",
]
