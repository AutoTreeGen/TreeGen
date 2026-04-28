"""Prompt-templates для LLM-операций (Phase 10.0+).

Каждый шаблон — текстовый файл с версионным заголовком:

    # version: v1
    # description: <краткое описание задачи>

    <тело промпта>

Версия записывается в audit-лог при каждом LLM-вызове, чтобы можно было
ретроактивно увидеть, какой именно промпт сгенерировал какой ответ.

Загрузка через ``load_prompt(name)`` — без I/O в hot path: шаблоны
кэшируются в памяти на module-level (LRU не нужен — у нас единицы
шаблонов).
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


@cache
def load_prompt(name: str) -> tuple[str, str]:
    """Загрузить промпт по имени файла (без расширения).

    Args:
        name: Имя файла без ``.txt`` (например, ``"place_normalization"``).

    Returns:
        Кортеж ``(version, body)``. ``version`` извлекается из заголовка
        ``# version: vN``; ``body`` — всё после header-блока.

    Raises:
        FileNotFoundError: Шаблон не найден.
        ValueError: Шаблон не содержит версионного заголовка.
    """
    path = _PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        msg = f"Prompt template not found: {path}"
        raise FileNotFoundError(msg)

    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    version: str | None = None
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("# version:"):
            version = stripped.removeprefix("# version:").strip()
        if stripped and not stripped.startswith("#"):
            body_start = i
            break

    if version is None:
        msg = f"Prompt {name!r} is missing '# version: vN' header"
        raise ValueError(msg)

    body = "\n".join(lines[body_start:]).strip()
    return version, body


__all__ = ["load_prompt"]
