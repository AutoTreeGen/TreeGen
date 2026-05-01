"""Phase 15.6 — Court-Ready Report.

Генерирует defensible PDF-отчёт по персоне / семье / линии предков с полным
evidence trail: vital stats с ссылками-сносками, relationships с типом
доказательства и confidence, таблица claim → source → quality, negative
findings (события без источника, gap'ы), signature page с methodology
statement.

Ничего не модифицирует в БД. Read-only side relative to existing schema
(persons / events / families / citations / sources / hypotheses).

Supersedes-note: ADR-0058 §«Endpoint живёт в parser-service»
распространяется и на reports — отдельный report-service не создаём,
эндпоинт живёт здесь же. Если объём отчётов вырастет (ancestry pack,
multi-tree, async batch), вынести в отдельный сервис как Phase 15.x.
"""

from __future__ import annotations

from parser_service.court_ready.api import router

__all__ = ["router"]
