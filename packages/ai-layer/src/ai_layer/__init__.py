"""AutoTreeGen ai-layer — Phase 10.0 skeleton.

Public API:

- ``AILayerConfig`` / ``AILayerDisabledError`` / ``AILayerConfigError``
- ``AnthropicClient`` / ``AnthropicCompletion``
- ``VoyageEmbeddingClient`` / ``EmbeddingResult``
- ``PromptRegistry`` / ``PromptTemplate`` / ``RenderedPrompt``
- ``HypothesisSuggester`` / ``HypothesisSuggestion`` / ``PersonFact`` /
  ``FabricatedEvidenceError``

См. ``README.md`` и ``docs/adr/0043-ai-layer-architecture.md``.
"""

from ai_layer.clients.anthropic_client import AnthropicClient, AnthropicCompletion
from ai_layer.clients.voyage_client import VoyageEmbeddingClient
from ai_layer.config import (
    AILayerConfig,
    AILayerConfigError,
    AILayerDisabledError,
)
from ai_layer.prompts.registry import (
    PromptRegistry,
    PromptTemplate,
    RenderedPrompt,
)
from ai_layer.types import EmbeddingResult, HypothesisSuggestion
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
    "FabricatedEvidenceError",
    "HypothesisSuggester",
    "HypothesisSuggestion",
    "PersonFact",
    "PromptRegistry",
    "PromptTemplate",
    "RenderedPrompt",
    "VoyageEmbeddingClient",
]
