"""Archive registry sub-package (Phase 22.1 / ADR-0074).

Маршрутизация:

* :mod:`archive_service.registry.router` — FastAPI-роутер.
* :mod:`archive_service.registry.schemas` — Pydantic-DTO для request/response.
* :mod:`archive_service.registry.repo` — DB-чтение (filter + rank).
* :mod:`archive_service.registry.scorer` — pure-функция ранжирования.
"""

from __future__ import annotations

from archive_service.registry import router

__all__ = ["router"]
