"""Phase 26.1 baseline engine entrypoint.

Public surface:

    >>> from inference_engine.engine import run_tree
    >>> output = run_tree(tree_dict)

Phase 26.1 contract (см. ADR-0084):

- Принимает один loaded tree JSON (см. ``data/test_corpus/trees/*.json``).
- Возвращает dict, удовлетворяющий ``output_schema.EngineOutput``.
- Baseline НЕ запускает реальные детекторы: ``engine_flags`` пустой,
  ``evaluation_results`` содержит ``{assertion_id: False}`` для всех
  assertion'ов tree-fixture'а.
- Baseline намеренно НЕ читает ``expected_engine_flags`` и не возвращает
  ``True`` для assertion'ов — иначе harness теряет diagnostic value.
- Phase 26.2+ подключит реальные детекторы через ``detectors/registry``
  (см. ``inference_engine.detectors``). Контракт engine_output не
  изменяется при подключении детекторов — они только обогащают списки.

Связь с существующим pipeline:

- ``compose_hypothesis`` (Phase 7.0) — pairwise-гипотезы, работает на уровне
  ``Hypothesis`` / ``Evidence``. Phase 26.x детекторы обернут его как
  один из источников ``relationship_claims``.
"""

from __future__ import annotations

from typing import Any

from inference_engine.output_schema import EngineOutput


def run_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """Запустить движок на одном tree-fixture.

    Args:
        tree: Loaded tree JSON (см. ``data/test_corpus/trees/*.json``).
            Минимум должно быть поле ``tree_id``; остальные поля
            (``evaluation_assertions``, ``input_gedcom_excerpt`` и т.д.)
            opt-in по мере подключения детекторов.

    Returns:
        Dict, удовлетворяющий ``EngineOutput`` schema. Phase 26.1 baseline
        возвращает все required keys пустыми; ``evaluation_results``
        заполнен ``{assertion_id: False}`` для каждого assertion из
        входного tree.

    Raises:
        ValueError: Если ``tree_id`` отсутствует или пустой.
    """
    tree_id = tree.get("tree_id")
    if not isinstance(tree_id, str) or not tree_id:
        msg = "tree must contain a non-empty 'tree_id' field"
        raise ValueError(msg)

    assertion_ids = _extract_assertion_ids(tree)

    output = EngineOutput(
        tree_id=tree_id,
        engine_flags=[],
        relationship_claims=[],
        merge_decisions=[],
        place_corrections=[],
        quarantined_claims=[],
        sealed_set_candidates=[],
        evaluation_results=dict.fromkeys(assertion_ids, False),
    )
    return output.model_dump()


def _extract_assertion_ids(tree: dict[str, Any]) -> list[str]:
    """Достать список assertion_id из ``tree['evaluation_assertions']``.

    Trees 04-20 хранят explicit ``assertion_id`` (формат ``eval_NN_NNN``).
    Trees 01-03 (early prototype format) хранят только ``assertion`` text;
    для них id синтезируется по индексу как ``eval_<tree_num>_<idx+1>``,
    чтобы runner мог стабильно matchить assertions между запусками.

    Дубликаты сохраняют порядок first-seen и игнорируются (один и тот же
    assertion_id засчитывается один раз).
    """
    tree_id = tree.get("tree_id", "")
    tree_num = _parse_tree_number(tree_id)

    seen: set[str] = set()
    out: list[str] = []
    raw_list = tree.get("evaluation_assertions") or []
    for idx, item in enumerate(raw_list):
        if not isinstance(item, dict):
            continue
        aid_raw = item.get("assertion_id")
        aid = (
            aid_raw
            if isinstance(aid_raw, str) and aid_raw
            else f"eval_{tree_num:02d}_{idx + 1:03d}"
        )
        if aid in seen:
            continue
        seen.add(aid)
        out.append(aid)
    return out


def _parse_tree_number(tree_id: str) -> int:
    """``tree_11_unknown_father`` -> ``11``. ``0`` если не парсится."""
    parts = tree_id.split("_")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


__all__ = ["run_tree"]
