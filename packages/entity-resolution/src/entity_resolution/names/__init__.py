"""entity_resolution.names — multilingual name engine (Phase 15.10 / ADR-0068).

Sub-package consolidating five orthogonal но related «name surgery» pieces:

* :mod:`patronymic` — split «Иван Иванович Петров» в ParsedName(given,
  patronymic, surname); поддержка ru / uk / by / pl.
* :mod:`transliterate` — Cyrillic↔Latin (BGN/ISO9/LOC), Polish/German
  diacritic fold, Yiddish↔Hebrew custom rules.
* :mod:`daitch_mokotoff` — thin re-export ``daitch_mokotoff`` из existing
  :mod:`entity_resolution.phonetic` под именем ``dm_soundex`` (без дублирования
  логики; canonical impl остаётся в phonetic.py per ADR-0015).
* :mod:`synonyms` — loader + reverse-index для ``data/icp_anchor_synonyms.json``
  (curated AJ / Slavic anchor surnames; ≥30 entries V1).
* :mod:`variants` — :func:`generate_archive_variants` объединяет всё выше.
* :mod:`match` — :class:`NameMatcher` ranks candidates через variants + DM.

Backward-compat: existing :mod:`entity_resolution.phonetic`,
:mod:`entity_resolution.string_matching`, :mod:`entity_resolution.persons` —
**не трогаются**. Phase 15.10 — **additive only**. Будущий PR (вне 15.10
scope) может опционально мигрировать ``persons.person_match_score`` на
:class:`NameMatcher`.
"""

from __future__ import annotations

from entity_resolution.names.daitch_mokotoff import dm_soundex
from entity_resolution.names.match import MatchResult, NameMatcher
from entity_resolution.names.patronymic import ParsedName, PatronymicParser
from entity_resolution.names.synonyms import load_icp_synonyms
from entity_resolution.names.transliterate import Transliterator
from entity_resolution.names.variants import generate_archive_variants

__all__ = [
    "MatchResult",
    "NameMatcher",
    "ParsedName",
    "PatronymicParser",
    "Transliterator",
    "dm_soundex",
    "generate_archive_variants",
    "load_icp_synonyms",
]
