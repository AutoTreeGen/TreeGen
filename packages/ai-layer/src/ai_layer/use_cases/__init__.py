"""AI use-cases (Phases 10.0–10.3 + 10.9a transcribe_audio)."""

from ai_layer.use_cases.explain_hypothesis import HypothesisExplainer
from ai_layer.use_cases.hypothesis_suggestion import (
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
from ai_layer.use_cases.transcribe_audio import (
    AudioTranscriber,
    TranscribeAudioInput,
    TranscribeAudioOutput,
)

__all__ = [
    "AudioTranscriber",
    "CandidateRecord",
    "EmptyInputError",
    "HypothesisExplainer",
    "HypothesisSuggester",
    "NameNormalizer",
    "NormalizationError",
    "PersonFact",
    "PlaceNormalizer",
    "RawInputTooLargeError",
    "TranscribeAudioInput",
    "TranscribeAudioOutput",
]
