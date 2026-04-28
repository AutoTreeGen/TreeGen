"""llm-services — Phase 10.0 AI-layer skeleton.

Public API:

* ``claude_client`` — фабрика настроенного ``AsyncAnthropic``.
* ``normalize_place_name`` — async-канонизация исторических топонимов.
* ``disambiguate_name_variants`` — async-группировка вариантов имени.
* ``NormalizedPlace`` / ``NameCluster`` — Pydantic-модели результатов.
* ``DEFAULT_MODEL`` / ``MissingApiKeyError`` — конфигурация и ошибки.

См. ADR-0030 «AI layer architecture» и README.md.
"""

from llm_services.client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    MissingApiKeyError,
    claude_client,
)
from llm_services.name_disambiguation import disambiguate_name_variants
from llm_services.place_normalization import normalize_place_name
from llm_services.types import NameCluster, NormalizedPlace

__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "MissingApiKeyError",
    "NameCluster",
    "NormalizedPlace",
    "claude_client",
    "disambiguate_name_variants",
    "normalize_place_name",
]
