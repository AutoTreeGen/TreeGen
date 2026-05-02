"""Voice-to-tree NLU extraction use-case (Phase 10.9b / ADR-0075).

3-pass pipeline над ``AudioSession.transcript_text``:

1. **entities** — ``create_person`` / ``add_place`` / ``flag_uncertain``
2. **relationships** — ``link_relationship`` / ``flag_uncertain``
3. **temporal-spatial** — ``add_event`` / ``flag_uncertain``

Каждый pass — отдельный Anthropic call с narrow tool-set'ом (per-pass
allowlist), без agentic-loop'а (см. ADR-0064 §4 + ADR-0075). Pass N+1
получает proposals из pass N как структурированный JSON-context в
user-message, не как tool_results — это критично, чтобы модель «не доводила»
свои pass-1 догадки.
"""

from __future__ import annotations

from ai_layer.use_cases.voice_to_tree_extract.config import (
    VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS,
    VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION,
    VOICE_EXTRACT_TOP_N_SEGMENTS,
)
from ai_layer.use_cases.voice_to_tree_extract.errors import (
    VoiceExtractCostCapError,
    VoiceExtractError,
)
from ai_layer.use_cases.voice_to_tree_extract.runner import (
    PASS_NAMES,
    PassProposal,
    VoiceExtractInput,
    VoiceExtractor,
    VoiceExtractResult,
)
from ai_layer.use_cases.voice_to_tree_extract.tools import (
    PASS_1_TOOLS,
    PASS_2_TOOLS,
    PASS_3_TOOLS,
    TOOL_FLAG_UNCERTAIN,
    TOOLS_BY_PASS,
    pass_allowed_tool_names,
)

__all__ = [
    "PASS_1_TOOLS",
    "PASS_2_TOOLS",
    "PASS_3_TOOLS",
    "PASS_NAMES",
    "TOOLS_BY_PASS",
    "TOOL_FLAG_UNCERTAIN",
    "VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS",
    "VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION",
    "VOICE_EXTRACT_TOP_N_SEGMENTS",
    "PassProposal",
    "VoiceExtractCostCapError",
    "VoiceExtractError",
    "VoiceExtractInput",
    "VoiceExtractResult",
    "VoiceExtractor",
    "pass_allowed_tool_names",
]
