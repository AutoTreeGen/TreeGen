"""AutoTreeGen ai-layer — Phase 10.0 skeleton + 10.1 explainer + 10.2 extraction + 10.3 normalization.

Public API:

- Config / kill-switch: ``AILayerConfig``, ``AILayerDisabledError``,
  ``AILayerConfigError``, ``ensure_ai_layer_enabled``,
  ``make_ai_layer_gate``.
- Clients: ``AnthropicClient``, ``AnthropicCompletion``, ``ImageInput``,
  ``VoyageEmbeddingClient``.
- Prompts: ``PromptRegistry``, ``PromptTemplate``, ``RenderedPrompt``.
- Types: ``EmbeddingResult``, ``HypothesisSuggestion``,
  ``ExtractionResult``, ``PersonExtract``, ``EventExtract``,
  ``RelationshipExtract``, ``EvidenceItem``, ``HypothesisExplanation``,
  ``HypothesisExplanationPayload``, ``HypothesisInput``,
  ``PersonSubject``, ``PlaceNormalization``, ``NameNormalization``,
  ``NormalizationResult``, ``CandidateMatch``.
- Budget / runs (Phase 10.2 / ADR-0059): ``BudgetLimits``,
  ``BudgetReport``, ``BudgetExceededError``, ``evaluate_budget``,
  ``AIRunStatus``, ``build_raw_response``.
- Pricing / telemetry (Phase 10.1): ``estimate_cost_usd``,
  ``log_ai_usage``.
- Use cases: ``HypothesisSuggester`` + companions; ``HypothesisExplainer``
  (Phase 10.1); ``SourceExtractor`` + ``SourceMetadata`` +
  extraction-error hierarchy; ``PlaceNormalizer`` / ``NameNormalizer``
  + ``CandidateRecord`` (Phase 10.3).

См. ``README.md``, ``docs/adr/0043-ai-layer-architecture.md``,
``docs/adr/0057-ai-hypothesis-explanation.md``,
``docs/adr/0059-ai-source-extraction.md`` и
``docs/adr/0060-ai-normalization.md``.
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
from ai_layer.pricing import estimate_cost_usd
from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    RenderedPrompt,
)
from ai_layer.runs import AIRunStatus, build_raw_response
from ai_layer.telemetry import log_ai_usage
from ai_layer.types import (
    CandidateMatch,
    EmbeddingResult,
    EventExtract,
    EvidenceItem,
    ExtractionResult,
    HypothesisExplanation,
    HypothesisExplanationPayload,
    HypothesisInput,
    HypothesisSuggestion,
    NameNormalization,
    NormalizationResult,
    PersonExtract,
    PersonSubject,
    PlaceNormalization,
    RelationshipExtract,
)
from ai_layer.use_cases.explain_hypothesis import HypothesisExplainer
from ai_layer.use_cases.hypothesis_suggestion import (
    FabricatedEvidenceError,
    HypothesisSuggester,
    PersonFact,
)
from ai_layer.use_cases.normalize import (
    CandidateRecord,
    EmptyInputError,
    NameNormalizer,
    NormalizationError,
    PlaceNormalizer,
    RawInputTooLargeError,
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
    "CandidateMatch",
    "CandidateRecord",
    "DocumentTooLargeError",
    "EmbeddingResult",
    "EmptyDocumentError",
    "EmptyInputError",
    "EventExtract",
    "EvidenceItem",
    "ExtractionResult",
    "FabricatedEvidenceError",
    "FabricatedQuoteError",
    "HypothesisExplainer",
    "HypothesisExplanation",
    "HypothesisExplanationPayload",
    "HypothesisInput",
    "HypothesisSuggester",
    "HypothesisSuggestion",
    "ImageInput",
    "NameNormalization",
    "NameNormalizer",
    "NormalizationError",
    "NormalizationResult",
    "PersonExtract",
    "PersonFact",
    "PersonSubject",
    "PlaceNormalization",
    "PlaceNormalizer",
    "PromptRegistry",
    "PromptTemplate",
    "RawInputTooLargeError",
    "RelationshipExtract",
    "RenderedPrompt",
    "SourceExtractionError",
    "SourceExtractor",
    "SourceMetadata",
    "VoyageEmbeddingClient",
    "build_raw_response",
    "ensure_ai_layer_enabled",
    "estimate_cost_usd",
    "evaluate_budget",
    "log_ai_usage",
    "make_ai_layer_gate",
]
