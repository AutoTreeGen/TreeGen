"""TreeGen MCP — Model Context Protocol server для AutoTreeGen (Phase 10.8).

Подключается к Claude Desktop / ChatGPT / любому MCP-host'у через stdio
и предоставляет read-only инструменты для запроса данных о дереве
пользователя. Backend — HTTP-вызовы к AutoTreeGen API gateway с
API-ключом пользователя.

Quickstart:

.. code-block:: bash

    export TREEGEN_API_URL=https://api.autotreegen.example.com
    export TREEGEN_API_KEY=atg_live_...
    uv run treegen-mcp

См. README для полной конфигурации Claude Desktop.
"""

from __future__ import annotations

from .auth import ApiCredentials, load_credentials
from .client import ApiError, AuthError, NotFoundError, TreeGenClient
from .config import TreeGenConfig, load_config
from .server import build_server, main, run_async

__all__ = [
    "ApiCredentials",
    "ApiError",
    "AuthError",
    "NotFoundError",
    "TreeGenClient",
    "TreeGenConfig",
    "build_server",
    "load_config",
    "load_credentials",
    "main",
    "run_async",
]
