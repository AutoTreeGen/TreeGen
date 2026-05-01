"""Ego Resolver (Phase 10.7b).

Rule-based резолвер relative-references: парсит фразы вроде «my wife»,
«брат матери жены», «Dvora» в ``ResolvedPerson`` с ``person.id`` и
confidence-score'ом. Foundation для 10.9b NLU extraction (sibling brief)
и Phase 10.7d Chat UI.

Public API: :func:`resolve_reference` + dataclass'ы :class:`TreeContext`,
:class:`PersonNames`, :class:`ResolvedPerson`, :class:`RelStep`.

Pure-function модуль: никаких I/O / LLM / network. Caller (api-gateway
или voice-pipeline) собирает ``TreeContext`` из БД и передаёт в резолвер.
LLM-fuzzy mode — out of scope для V1 (см. ROADMAP §10.7b).
"""

from __future__ import annotations

from ai_layer.ego_resolver.resolver import resolve_reference
from ai_layer.ego_resolver.types import (
    PersonNames,
    RelStep,
    ResolvedPerson,
    TreeContext,
)

__all__ = [
    "PersonNames",
    "RelStep",
    "ResolvedPerson",
    "TreeContext",
    "resolve_reference",
]
