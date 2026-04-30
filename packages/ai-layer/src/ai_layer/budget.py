"""Budget enforcement для AI-вызовов (Phase 10.2 / ADR-0059).

Generic-модуль: не зависит от sqlalchemy и не привязан к конкретной
таблице. Caller'ы (parser-service для source-extraction; inference-service
для будущего hypothesis-runner) сами считают свои usage-метрики и
передают их в :class:`BudgetReport`. Решение «over budget или нет» —
здесь, в одном месте, чтобы все use-case'ы AI-слоя были симметричны.

Default-лимиты — консервативные и meant как safe-floor; product-уровень
override'ит через `BudgetLimits.override_from_env(prefix="AI_BUDGET_")`
или явный конструктор.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

DEFAULT_MAX_RUNS_PER_DAY: Final[int] = 10
DEFAULT_MAX_TOKENS_PER_MONTH: Final[int] = 100_000


class BudgetExceededError(RuntimeError):
    """Поднимается, когда usage превышает разрешённый бюджет.

    Атрибуты ``limit_kind`` и ``limit_value`` позволяют caller'у
    отрендерить осмысленную 429-ошибку: «вы достигли лимита 10 вызовов
    в день, попробуйте завтра».
    """

    def __init__(self, limit_kind: str, limit_value: int, current_value: int) -> None:
        self.limit_kind = limit_kind
        self.limit_value = limit_value
        self.current_value = current_value
        super().__init__(
            f"AI budget exceeded: {limit_kind}={current_value} >= limit={limit_value}",
        )


@dataclass(frozen=True)
class BudgetLimits:
    """Конфигурация бюджетных порогов.

    Attributes:
        max_runs_per_day: Максимум AI-вызовов от одного user'а за
            скользящие 24 часа. ``0`` отключает rate limit.
        max_tokens_per_month: Максимум суммарных tokens (input+output)
            за скользящие 30 дней. ``0`` отключает token budget.
    """

    max_runs_per_day: int = DEFAULT_MAX_RUNS_PER_DAY
    max_tokens_per_month: int = DEFAULT_MAX_TOKENS_PER_MONTH

    @classmethod
    def from_env(
        cls,
        env: dict[str, str] | None = None,
        *,
        prefix: str = "AI_BUDGET_",
    ) -> BudgetLimits:
        """Читает лимиты из ENV; отсутствующие — дефолты.

        Поддерживаемые ключи (под ``prefix``):

        * ``{prefix}MAX_RUNS_PER_DAY`` — int.
        * ``{prefix}MAX_TOKENS_PER_MONTH`` — int.
        """
        source = env if env is not None else dict(os.environ)
        return cls(
            max_runs_per_day=_parse_int(
                source.get(f"{prefix}MAX_RUNS_PER_DAY"),
                DEFAULT_MAX_RUNS_PER_DAY,
            ),
            max_tokens_per_month=_parse_int(
                source.get(f"{prefix}MAX_TOKENS_PER_MONTH"),
                DEFAULT_MAX_TOKENS_PER_MONTH,
            ),
        )


@dataclass(frozen=True)
class BudgetReport:
    """Snapshot текущего usage'а одного user'а.

    Caller отвечает за computed-значения (обычно один SQL за обоими).
    Этот модуль не делает round-trip в БД сам — namespace-чистый
    относительно ORM.
    """

    runs_in_last_24h: int
    tokens_in_last_30d: int
    limits: BudgetLimits

    @property
    def is_rate_limited(self) -> bool:
        """True если runs_in_last_24h ≥ max_runs_per_day."""
        if self.limits.max_runs_per_day <= 0:
            return False
        return self.runs_in_last_24h >= self.limits.max_runs_per_day

    @property
    def is_over_token_budget(self) -> bool:
        """True если tokens_in_last_30d ≥ max_tokens_per_month."""
        if self.limits.max_tokens_per_month <= 0:
            return False
        return self.tokens_in_last_30d >= self.limits.max_tokens_per_month

    @property
    def remaining_runs(self) -> int:
        """Сколько ещё вызовов можно сделать сегодня (нижняя оценка)."""
        if self.limits.max_runs_per_day <= 0:
            return -1  # unlimited sentinel
        return max(0, self.limits.max_runs_per_day - self.runs_in_last_24h)

    @property
    def remaining_tokens(self) -> int:
        """Сколько ещё tokens можно потратить за месяц."""
        if self.limits.max_tokens_per_month <= 0:
            return -1
        return max(0, self.limits.max_tokens_per_month - self.tokens_in_last_30d)


def evaluate_budget(report: BudgetReport) -> None:
    """Бросить :class:`BudgetExceededError` если report за рамками.

    Проверяет rate-limit перед token-budget — это даёт UI более
    actionable-сообщение («подожди, retry через час» vs. «ты съел
    месячный лимит»).
    """
    if report.is_rate_limited:
        raise BudgetExceededError(
            limit_kind="runs_per_day",
            limit_value=report.limits.max_runs_per_day,
            current_value=report.runs_in_last_24h,
        )
    if report.is_over_token_budget:
        raise BudgetExceededError(
            limit_kind="tokens_per_month",
            limit_value=report.limits.max_tokens_per_month,
            current_value=report.tokens_in_last_30d,
        )


def _parse_int(value: str | None, default: int) -> int:
    """Безопасно распарсить ENV-int; пустая строка / мусор → default."""
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


__all__ = [
    "DEFAULT_MAX_RUNS_PER_DAY",
    "DEFAULT_MAX_TOKENS_PER_MONTH",
    "BudgetExceededError",
    "BudgetLimits",
    "BudgetReport",
    "evaluate_budget",
]
