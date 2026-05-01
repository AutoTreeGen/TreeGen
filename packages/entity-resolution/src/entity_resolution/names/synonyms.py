"""ICP-anchor synonym loader (Phase 15.10 / ADR-0068).

Reads ``data/icp_anchor_synonyms.json`` (curated archive-spelling synonyms
for Eastern European Jewish + Slavic anchor surnames) и строит reverse-
index: ``canonical_form(variant) → set[str]`` всех variants.

Reverse-index design: caller передаёт **любую** форму (English, Cyrillic,
Hebrew, Polish-folded, ...) — она канонизируется и резолвится в общий set.
Это устраняет двойной look-up «сначала найди canonical, потом возьми
variants» и работает симметрично для любого input-script'а.

Singleton с lru_cache: JSON читается один раз, в module-load timing'е
ничего не тяжёлого нет (~3 KB файл, ~30 keys). Тесты могут сбросить
кэш через :func:`load_icp_synonyms.cache_clear` и подменить путь через
:func:`_resolve_data_path` (override-able через monkey-patch).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Final

from unidecode import unidecode

# Файл лежит рядом с модулями ``names/*`` чтобы поставлялся как
# package-data при ``pip install entity-resolution``. Hatch ``packages =
# ["src/entity_resolution"]`` уже включает всё содержимое sub-package'а.
_DATA_FILENAME: Final[str] = "icp_anchor_synonyms.json"


def _resolve_data_path() -> Path:
    """Путь до ``data/icp_anchor_synonyms.json`` относительно этого модуля.

    Вынесено отдельной функцией для возможности monkey-patch'а в тестах
    (override на временный JSON с custom-фикстурами).
    """
    return Path(__file__).parent / "data" / _DATA_FILENAME


def canonical_form(name: str) -> str:
    """Канонизация для синонимного look-up'а.

    Алгоритм:
    1. ``unidecode`` фолдит диакритику и нелатиницу в ASCII (Левитин →
       Levitin, Müller → Muller, Łukasz → Lukasz). Hebrew Unidecode-результат
       не идеален, но воспроизводимый — то же самое выйдет для каждого
       прохода.
    2. lower-case + strip whitespace.

    Не используется для display'а — это ключ для словаря synonym-index'а.
    """
    # ``unidecode`` returns Any в их stubs (если есть); каст на ``str`` для
    # mypy strict-чистоты.
    return str(unidecode(name)).strip().lower()


@lru_cache(maxsize=1)
def load_icp_synonyms() -> dict[str, frozenset[str]]:
    """Прочитать ``icp_anchor_synonyms.json`` и построить reverse-index.

    Returns:
        ``dict`` где ключ — :func:`canonical_form` любого variant'а, значение —
        ``frozenset`` всех variants для этой anchor-группы (включая canonical
        key). Frozenset — чтобы caller'ы не мутировали shared cache случайно.

    Notes:
        Запись ``"_meta"`` в JSON игнорируется (resvd для версии / описания).
        Если variant встречается в двух anchor-группах (теоретически возможно
        при ошибках курации) — побеждает последняя группа в JSON-порядке;
        тесты должны ловить такие коллизии.
    """
    raw = json.loads(_resolve_data_path().read_text(encoding="utf-8"))
    index: dict[str, frozenset[str]] = {}
    for canonical_key, variants in raw.items():
        if canonical_key.startswith("_"):
            # ``_meta`` и любые другие подчёркнутые ключи — служебные.
            continue
        full_set: frozenset[str] = frozenset({canonical_key, *variants})
        for variant in full_set:
            index[canonical_form(variant)] = full_set
    return index


__all__ = ["canonical_form", "load_icp_synonyms"]
