"""Phase 27.1 — claim/decision builders are intentionally deferred to
Phase 27.2.

Контекст: Phase 27.1 ставит safe-accessor extractors и diagnostic
test, чтобы exposed cheat surface был visible. Builder'ы для
``relationship_claims`` / ``merge_decisions`` / ``place_corrections``
/ ``quarantined_claims`` / ``sealed_set_candidates`` — отдельный
вопрос: их точная shape API будет понятна только после того, как
первый detector мигрирует на extractors и реально потребует helper.

Phase 27.2 (первая миграция, candidate
``historical_place_jurisdiction.py``) добавит первый builder,
scope'нутый под dict-shape, который этот detector реально
emitter'ит. Subsequent migration'ы либо переиспользуют, либо
добавят siblings.

Пока модуль пустой — это не error, а декларация scope'а.
См. ADR-0097 §"Builders deferred to Phase 27.2".
"""

from __future__ import annotations

__all__: list[str] = []
