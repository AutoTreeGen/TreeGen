"""Tests-local conftest: makes `tests/` importable as a flat module dir.

Pytest конфиг ставит ``--import-mode=importlib``, при этом sibling-модули
в ``tests/`` (например ``_evidence_helpers``) не подхватываются
автоматически. Добавляем директорию в ``sys.path`` один раз — чтобы тесты
могли делать ``from _evidence_helpers import ...``.

Future Phase 27.2+ migration'ы используют ``_evidence_helpers``
(``poison_answer_key`` / ``assert_detector_ignores_answer_key``) из
тестов конкретных детекторов; этот conftest'у удобнее всего держать
sys.path-вставку, чтобы не плодить per-test-file boilerplate.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
