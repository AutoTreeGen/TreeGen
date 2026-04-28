"""``GET /metrics`` — Prometheus exposition endpoint (Phase 9.0).

Отдаёт содержимое default ``prometheus_client.REGISTRY`` в стандартном
text-формате (``text/plain; version=0.0.4``). Scrape-config — см.
runbook (отдельный follow-up).

Сами collectors определены в ``parser_service.services.metrics`` и
обновляются inline в ``hypothesis_runner`` / ``import_runner`` /
``familysearch_importer`` / ``dedup_finder`` / ``api/hypotheses``.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

# Импорт ради side-effect: регистрация collector'ов в default REGISTRY.
# Без него GET /metrics на холодном процессе вернёт пустой набор treegen_*
# метрик (они появляются только после первого .inc()/.observe()).
import parser_service.services.metrics  # noqa: F401

router = APIRouter()


@router.get(
    "/metrics",
    tags=["meta"],
    summary="Prometheus exposition endpoint",
    response_class=Response,
)
async def metrics() -> Response:
    """Снять снапшот метрик из default registry."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
