"""Engine entrypoint для evaluation harness.

Public surface:

    >>> from inference_engine.engine import run_tree
    >>> output = run_tree(tree_dict)

Контракт (ADR-0084 + ADR-0085):

- Принимает один loaded tree JSON (см. ``data/test_corpus/trees/*.json``).
- Возвращает dict, удовлетворяющий ``output_schema.EngineOutput``.
- Phase 26.1 baseline возвращал пустой output;
  Phase 26.2+ запускает зарегистрированные детекторы через
  ``inference_engine.detectors.registry.run_all`` и мерджит их
  ``DetectorResult`` в финальный ``EngineOutput``.
- ``evaluation_results`` инициализируется ``{assertion_id: False}``
  для каждого assertion из tree, после чего детектор может пометить
  отдельные id'ы True (но никогда не должен помечать массово True по
  какому-нибудь tree_id).

Связь с существующим pipeline:

- ``compose_hypothesis`` (Phase 7.0) — pairwise-гипотезы, работает на уровне
  ``Hypothesis`` / ``Evidence``. Phase 26.x детекторы обернут его как
  один из источников ``relationship_claims``.
"""

from __future__ import annotations

from typing import Any

from inference_engine.detectors.registry import run_all
from inference_engine.output_schema import EngineOutput


def run_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """Запустить движок на одном tree-fixture.

    Args:
        tree: Loaded tree JSON (см. ``data/test_corpus/trees/*.json``).
            Минимум должно быть поле ``tree_id``; остальные поля
            (``evaluation_assertions``, ``input_gedcom_excerpt`` и т.д.)
            opt-in по мере подключения детекторов.

    Returns:
        Dict, удовлетворяющий ``EngineOutput`` schema. Все required keys
        присутствуют. ``evaluation_results`` инициализирован
        ``{assertion_id: False}`` для каждого assertion и затем перекрыт
        ``True``-значениями от детекторов, поддерживающих эти assertion'ы.

    Raises:
        ValueError: Если ``tree_id`` отсутствует или пустой.
    """
    tree_id = tree.get("tree_id")
    if not isinstance(tree_id, str) or not tree_id:
        msg = "tree must contain a non-empty 'tree_id' field"
        raise ValueError(msg)

    assertion_ids = _extract_assertion_ids(tree)
    evaluation_results: dict[str, bool] = dict.fromkeys(assertion_ids, False)

    detector_output = run_all(tree)

    # Detector-эмиссии, относящиеся к assertion_id, которых нет во входном
    # tree, игнорируются — runner всё равно их не оценит, и засорять
    # evaluation_results bogus-ключами не нужно.
    for aid, value in detector_output.evaluation_results.items():
        if aid in evaluation_results:
            evaluation_results[aid] = value

    output = EngineOutput(
        tree_id=tree_id,
        engine_flags=list(detector_output.engine_flags),
        relationship_claims=list(detector_output.relationship_claims),
        merge_decisions=list(detector_output.merge_decisions),
        place_corrections=list(detector_output.place_corrections),
        quarantined_claims=list(detector_output.quarantined_claims),
        sealed_set_candidates=list(detector_output.sealed_set_candidates),
        evaluation_results=evaluation_results,
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
