"""entity_resolution — pure-function алгоритмы дедупликации (ADR-0015).

Public API: импортируется через ``entity_resolution.<module>``. Здесь —
только re-exports того, что нужно потребителю (parser-service / тесты),
чтобы не путать internal helpers.
"""

from __future__ import annotations

from entity_resolution.blocking import block_by_dm
from entity_resolution.persons import PersonForMatching, person_match_score
from entity_resolution.phonetic import daitch_mokotoff, soundex
from entity_resolution.places import place_match_score
from entity_resolution.sources import source_match_score
from entity_resolution.string_matching import (
    levenshtein_ratio,
    token_set_ratio,
    weighted_score,
)

__version__ = "0.1.0"

__all__ = [
    "PersonForMatching",
    "__version__",
    "block_by_dm",
    "daitch_mokotoff",
    "levenshtein_ratio",
    "person_match_score",
    "place_match_score",
    "soundex",
    "source_match_score",
    "token_set_ratio",
    "weighted_score",
]
