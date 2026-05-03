"""Detectors — Phase 26.2+ слот для deterministic engine flag emitters.

Phase 26.1 (этот PR) содержит только baseline ``engine.run_tree`` без
реальных детекторов. Phase 26.2+ будет подключать детекторы по одному
через registry, аналогично ``inference_engine.rules.registry``:

- npe_via_dna (tree 11)
- gedcom_safe_merge (tree 15)
- metric_book_ocr_correction (tree 16)
- revision_list_household (tree 17)
- immigration_name_change_myth (tree 18)
- famous_line_overclaim_filter (tree 19)
- full_pipeline_sealed_set (tree 20)

Контракт detector'а (preview, fixируется в Phase 26.2):

    def detect(tree: dict, ctx: DetectorContext) -> DetectorResult: ...

где ``DetectorResult`` отдаёт списки ``engine_flags``, ``relationship_claims``
и т.д., которые ``engine.run_tree`` мерджит в финальный ``EngineOutput``.

Этот ``__init__.py`` намеренно пустой: импорт ``inference_engine.detectors``
не должен подтягивать ничего тяжёлого до Phase 26.2.
"""

from __future__ import annotations

__all__: list[str] = []
