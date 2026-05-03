"""Cost cap константы для voice extraction (ADR-0075 §«Cost cap»).

Hard-coded дефолты — переопределяются через kwargs в :class:`VoiceExtractor`,
если caller хочет нестандартный per-tree budget. Для production-агрегатов
(billing) cap'ы — последняя линия защиты от runaway-cost'а; основные
лимиты — на уровне Tree.subscription tier (Phase 12.x).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final

# Per-pass token cap. Pass 1 видит весь top-N transcript; pass 2/3 видят
# transcript + accumulated context (persons, places). 4_000 — sweet-spot
# по ADR-0064 §4 outline (top-N + truncate); если больше — модель «теряется»
# в собственном context'е.
VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS: Final[int] = 4_000

# Total cost cap на одну session (3 pass'а суммарно). Sonnet 4.6: ~$3/Mtok in,
# ~$15/Mtok out. 3 × (4k in × $3 + 1k out × $15) = ~$0.081 — закладываем 2.5×
# запас под edge-кейсы (large output, model upgrade). Cap на $0.20 — sane
# default; tier-based override в 12.x.
VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION: Final[Decimal] = Decimal("0.20")

# Top-N сегментов transcript'а (split по ``\n\n``). Whisper сам не сегментирует
# — caller (этот pipeline) разрезает руками и берёт первые N. 30 сегментов
# покрывает типичную сессию 2-5 минут с длинными паузами между темами.
VOICE_EXTRACT_TOP_N_SEGMENTS: Final[int] = 30

__all__ = [
    "VOICE_EXTRACT_MAX_INPUT_TOKENS_PER_PASS",
    "VOICE_EXTRACT_MAX_TOTAL_USD_PER_SESSION",
    "VOICE_EXTRACT_TOP_N_SEGMENTS",
]
