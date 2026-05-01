"""MCP-инструменты treegen-mcp.

Каждый tool — это async-функция, принимающая :class:`TreeGenClient` и
аргументы tool'а, возвращающая JSON-сериализуемый ``dict``. Регистрация
в FastMCP происходит в ``server.py`` — здесь только бизнес-логика
вызова HTTP.

Изолировать tools от FastMCP-декоратора нужно для:

* unit-тестов: тестируем чистую функцию без MCP-runtime;
* документации: каждый файл = self-contained описание одной "фичи".
"""

from __future__ import annotations

from .get_person import get_person
from .get_tree_context import get_tree_context
from .list_my_trees import list_my_trees
from .resolve_person import resolve_person
from .search_persons import search_persons

__all__ = [
    "get_person",
    "get_tree_context",
    "list_my_trees",
    "resolve_person",
    "search_persons",
]
