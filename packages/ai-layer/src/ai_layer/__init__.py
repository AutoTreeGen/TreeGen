"""AutoTreeGen ai-layer — Phase 10.0 skeleton + Phase 10.1 explainer.

Public API:

- ``AILayerConfig`` / ``AILayerDisabledError`` / ``AILayerConfigError``
- ``AnthropicClient`` / ``AnthropicCompletion``
- ``VoyageEmbeddingClient`` / ``EmbeddingResult``
- ``PromptRegistry`` / ``PromptTemplate`` / ``RenderedPrompt``
- ``HypothesisSuggester`` / ``HypothesisSuggestion`` / ``PersonFact`` /
  ``FabricatedEvidenceError``
- ``HypothesisExplainer`` / ``HypothesisInput`` / ``HypothesisExplanation`` /
  ``PersonSubject`` / ``EvidenceItem`` (Phase 10.1)
- ``estimate_cost_usd`` (Phase 10.1)

См. ``README.md``, ``docs/adr/0043-ai-layer-architecture.md`` и
``docs/adr/0057-ai-hypothesis-explanation.md``.
"""

from ai_layer.clients.anthropic_client import AnthropicClient, AnthropicCompletion
from ai_layer.clients.voyage_client import VoyageEmbeddingClient
from ai_layer.config import (
    AILayerConfig,
    AILayerConfigError,
    AILayerDisabledError,
)
from ai_layer.pricing import estimate_cost_usd
from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    RenderedPrompt,
)
from ai_layer.types import (
    EmbeddingResult,
    EvidenceItem,
    HypothesisExplanation,
    HypothesisExplanationPayload,
    HypothesisInput,
    HypothesisSuggestion,
    PersonSubject,
)
from ai_layer.use_cases.explain_hypothesis import HypothesisExplainer
from ai_layer.use_cases.hypothesis_suggestion import (
    FabricatedEvidenceError,
    HypothesisSuggester,
    PersonFact,
)

__all__ = [
    "AILayerConfig",
    "AILayerConfigError",
    "AILayerDisabledError",
    "AnthropicClient",
    "AnthropicCompletion",
    "EmbeddingResult",
    "EvidenceItem",
    "FabricatedEvidenceError",
    "HypothesisExplainer",
    "HypothesisExplanation",
    "HypothesisExplanationPayload",
    "HypothesisInput",
    "HypothesisSuggester",
    "HypothesisSuggestion",
    "PersonFact",
    "PersonSubject",
    "PromptRegistry",
    "PromptTemplate",
    "RenderedPrompt",
    "VoyageEmbeddingClient",
    "estimate_cost_usd",
]
