"""Match-list CSV importers (Phase 16.3 / ADR-0072).

Per-platform CSV parsers for «people who match my DNA» exports from
Ancestry, 23andMe, MyHeritage, FTDNA, and GEDmatch. Each parser is a
pure function that returns a list of :class:`MatchListEntry` (frozen
Pydantic model). Persistence — отдельный слой в dna-service.

Anti-drift (ADR-0072):
* No scraping. CSV upload only.
* No cross-platform identity resolution (Phase 16.5).
* Trust the platform's predicted-relationship string; we just bucket
  it into :class:`PredictedRelationship` for aggregation.
* ``raw_payload`` (full CSV row) preserved verbatim for re-parse.
"""

from __future__ import annotations

from dna_analysis.match_list.dispatcher import parse_match_list
from dna_analysis.match_list.models import MatchListEntry
from dna_analysis.match_list.relationship import normalise_relationship

__all__ = [
    "MatchListEntry",
    "normalise_relationship",
    "parse_match_list",
]
