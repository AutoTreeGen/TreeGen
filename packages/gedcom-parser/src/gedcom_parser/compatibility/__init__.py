"""GEDCOM Compatibility Simulator (Phase 5.6).

Предсказывает, как целевая платформа (Ancestry, MyHeritage, FamilySearch,
Gramps) воспримет произвольный :class:`gedcom_parser.GedcomDocument`: какие
теги будут сброшены, какие символы перекодированы, какие структуры
утрачены.

Высокоуровневое API:

    >>> from gedcom_parser import parse_document_file
    >>> from gedcom_parser.compatibility import simulate
    >>> doc = parse_document_file("tree.ged")
    >>> report = simulate(doc, target="ancestry")
    >>> report.estimated_loss_pct
    0.07
    >>> [d.tag_path for d in report.tag_drops][:3]
    ['INDI._UID', 'INDI._UID', 'INDI._FSFTID']

Правила перевозятся YAML-файлами в ``compatibility/rules/`` и грузятся
через :func:`load_rules`. Симулятор pure-Python, IO ограничено чтением
package data — никаких внешних запросов, никаких записей на диск.

Out of scope (Phase 5.6b или позже):

* Round-trip simulation: сгенерировать GED, который таргет реально
  получит после import → export.
* Custom user rules.
* Web UI.
"""

from __future__ import annotations

from gedcom_parser.compatibility.rules import (
    DropRule,
    EncodingRule,
    StructureRule,
    TargetRules,
    load_rules,
)
from gedcom_parser.compatibility.simulator import (
    TARGETS,
    CompatibilityReport,
    EncodingIssue,
    StructureChange,
    TagDrop,
    Target,
    simulate,
)

__all__ = [
    "TARGETS",
    "CompatibilityReport",
    "DropRule",
    "EncodingIssue",
    "EncodingRule",
    "StructureChange",
    "StructureRule",
    "TagDrop",
    "Target",
    "TargetRules",
    "load_rules",
    "simulate",
]
