"""Global pytest conftest — обработка cross-cutting test isolation.

Phase 13.2 (ADR-0053): slowapi rate-limiter использует in-memory storage,
которое отравляет состояние между тест-файлами одного процесса. Здесь мы
выставляем ``RATE_LIMITING_ENABLED=false`` ДО collection и импорта app-
модулей — ``shared_models.security.apply_security_middleware`` читает
эту env при ``Limiter()``-инициализации.

Тесты, которые проверяют сам rate-limiter (``packages/shared-models/tests/
test_security.py``), включают его обратно через monkeypatch + явный
``apply_security_middleware`` в собственной фикстуре.
"""

from __future__ import annotations

import os

# Disable slowapi rate-limit during tests by default. Real rate-limit logic
# is unit-tested directly in test_security.py — services не должны страдать
# от истощения per-process бюджета между тест-файлами.
os.environ.setdefault("RATE_LIMITING_ENABLED", "false")
