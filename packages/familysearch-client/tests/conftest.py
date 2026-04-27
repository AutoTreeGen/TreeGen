"""Конфигурация pytest для familysearch-client.

Шаренные фикстуры PKCE/HTTP появятся в Task 3/4 PR; пока — пусто.
Маркер ``familysearch_real`` регистрируется в корневом ``pyproject.toml``
(``[tool.pytest.ini_options].markers``), так что отдельной регистрации
здесь не нужно.
"""

from __future__ import annotations
