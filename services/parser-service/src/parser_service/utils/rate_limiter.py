"""In-memory sliding-window rate limiter (Phase 11.2).

Используется только для public-эндпоинтов (``GET /public/trees/{token}``),
где аутентификации нет и нужна минимальная защита от scrape. Намеренно
прост: dict[ip → list[timestamp]] + sliding window. Не distributed —
multi-worker корректность не гарантируется (каждый worker считает свой
budget). Этого достаточно для staging single-pod deploy; при scale-out
заменим на slowapi+Redis в отдельной фазе.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from typing import Final


class InMemoryRateLimiter:
    """Sliding window rate limiter с per-key (IP) state.

    Args:
        max_requests: Сколько запросов разрешено в окне.
        window_seconds: Длина окна в секундах.

    Thread-safe через единый Lock — окей под GIL и низкий QPS.
    Для high-QPS нужен Redis-based limiter.
    """

    def __init__(self, *, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, *, now: float | None = None) -> bool:
        """Возвращает True если запрос разрешён, False если превышен лимит.

        Side-effect: при True добавляет timestamp в bucket; при False — нет.
        Это значит, что rate-limited клиент не наращивает «штраф» бесконечно.
        """
        ts = now if now is not None else time.monotonic()
        cutoff = ts - self._window
        with self._lock:
            bucket = self._buckets[key]
            # Окно скользит — вычистить устаревшие timestamp'ы.
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(ts)
            return True

    def reset(self, keys: Iterable[str] | None = None) -> None:
        """Очистить bucket'ы. Без аргументов — все. Используется в тестах."""
        with self._lock:
            if keys is None:
                self._buckets.clear()
            else:
                for k in keys:
                    self._buckets.pop(k, None)


# Public-share endpoint: 60 запросов в минуту per-IP. Достаточно щедро
# для UI-навигации (open page → fetch tree → maybe re-fetch); зажато
# достаточно чтобы scrape-bot упёрся быстро.
PUBLIC_SHARE_RATE_LIMIT_MAX: Final[int] = 60
PUBLIC_SHARE_RATE_LIMIT_WINDOW_SECONDS: Final[float] = 60.0

public_share_rate_limiter = InMemoryRateLimiter(
    max_requests=PUBLIC_SHARE_RATE_LIMIT_MAX,
    window_seconds=PUBLIC_SHARE_RATE_LIMIT_WINDOW_SECONDS,
)
