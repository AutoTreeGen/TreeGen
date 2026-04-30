"""AutoTreeGen ai-layer — Phase 10.0 skeleton + Phase 10.2 source extraction.

Public API:

- Config / kill-switch: ``AILayerConfig``, ``AILayerDisabledError``,
  ``AILayerConfigError``, ``ensure_ai_layer_enabled``,
  ``make_ai_layer_gate``.
- Clients: ``AnthropicClient``, ``AnthropicCompletion``, ``ImageInput``,
  ``VoyageEmbeddingClient``.
- Prompts: ``PromptRegistry``, ``PromptTemplate``, ``RenderedPrompt``.
- Types: ``EmbeddingResult``, ``HypothesisSuggestion``,
  ``ExtractionResult``, ``PersonExtract``, ``EventExtract``,
  ``RelationshipExtract``.
- Budget / runs (Phase 10.2 / ADR-0059): ``BudgetLimits``,
  ``BudgetReport``, ``BudgetExceededError``, ``evaluate_budget``,
  ``AIRunStatus``, ``build_raw_response``.
- Use cases: ``HypothesisSuggester`` + companions; ``SourceExtractor`` +
  ``SourceMetadata`` + extraction-error hierarchy.

См. ``README.md`` и ``docs/adr/0043-ai-layer-architecture.md`` /
``docs/adr/0059-ai-source-extraction.md``.
"""

from ai_layer.budget import (
    BudgetExceededError,
    BudgetLimits,
    BudgetReport,
    evaluate_budget,
)
from ai_layer.clients.anthropic_client import (
    AnthropicClient,
    AnthropicCompletion,
    ImageInput,
)
from ai_layer.clients.voyage_client import VoyageEmbeddingClient
from ai_layer.config import (
    AILayerConfig,
    AILayerConfigError,
    AILayerDisabledError,
)
from ai_layer.gates import ensure_ai_layer_enabled, make_ai_layer_gate
from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    RenderedPrompt,
)
from ai_layer.runs import AIRunStatus, build_raw_response
from ai_layer.types import (
    EmbeddingResult,
    EventExtract,
    ExtractionResult,
    HypothesisSuggestion,
    PersonExtract,
    RelationshipExtract,
)
from ai_layer.use_cases.hypothesis_suggestion import (
    FabricatedEvidenceError,
    HypothesisSuggester,
    PersonFact,
)
from ai_layer.use_cases.source_extraction import (
    DocumentTooLargeError,
    EmptyDocumentError,
    FabricatedQuoteError,
    SourceExtractionError,
    SourceExtractor,
    SourceMetadata,
)

__all__ = [
    "AILayerConfig",
    "AILayerConfigError",
    "AILayerDisabledError",
    "AIRunStatus",
    "AnthropicClient",
    "AnthropicCompletion",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetReport",
    "DocumentTooLargeError",
    "EmbeddingResult",
    "EmptyDocumentError",
    "EventExtract",
    "ExtractionResult",
    "FabricatedEvidenceError",
    "FabricatedQuoteError",
    "HypothesisSuggester",
    "HypothesisSuggestion",
    "ImageInput",
    "PersonExtract",
    "PersonFact",
    "PromptRegistry",
    "PromptTemplate",
    "RelationshipExtract",
    "RenderedPrompt",
    "SourceExtractionError",
    "SourceExtractor",
    "SourceMetadata",
    "VoyageEmbeddingClient",
    "build_raw_response",
    "ensure_ai_layer_enabled",
    "evaluate_budget",
    "make_ai_layer_gate",
]
