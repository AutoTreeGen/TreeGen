"""Thin re-export Daitch-Mokotoff Soundex для names/-subpackage.

Canonical implementation живёт в :mod:`entity_resolution.phonetic` (ADR-0015,
predates Phase 15.10). Здесь — только alias под именем ``dm_soundex``,
чтобы потребители ``names/*`` модулей могли использовать единый импорт-
паттерн ``from entity_resolution.names import dm_soundex`` (см. ADR-0068
§«DM Re-export» и связанные обоснования).

**Не дублируй логику здесь.** Любые улучшения DM-таблицы — в
:mod:`entity_resolution.phonetic`.
"""

from __future__ import annotations

from entity_resolution.phonetic import daitch_mokotoff as dm_soundex

__all__ = ["dm_soundex"]
