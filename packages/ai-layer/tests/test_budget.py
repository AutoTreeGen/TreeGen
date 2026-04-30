"""Тесты ``ai_layer.budget`` — generic-логика без DB.

Покрывают: limits-from-env, threshold-evaluation, edge cases (zero =
unlimited), error class.
"""

from __future__ import annotations

import pytest
from ai_layer.budget import (
    DEFAULT_MAX_RUNS_PER_DAY,
    DEFAULT_MAX_TOKENS_PER_MONTH,
    BudgetExceededError,
    BudgetLimits,
    BudgetReport,
    evaluate_budget,
)


def test_limits_default_values() -> None:
    limits = BudgetLimits()
    assert limits.max_runs_per_day == DEFAULT_MAX_RUNS_PER_DAY
    assert limits.max_tokens_per_month == DEFAULT_MAX_TOKENS_PER_MONTH


def test_limits_from_env_with_overrides() -> None:
    env = {
        "AI_BUDGET_MAX_RUNS_PER_DAY": "5",
        "AI_BUDGET_MAX_TOKENS_PER_MONTH": "50000",
    }
    limits = BudgetLimits.from_env(env)
    assert limits.max_runs_per_day == 5
    assert limits.max_tokens_per_month == 50_000


def test_limits_from_env_falls_back_on_garbage() -> None:
    env = {"AI_BUDGET_MAX_RUNS_PER_DAY": "  not-a-number  "}
    limits = BudgetLimits.from_env(env)
    assert limits.max_runs_per_day == DEFAULT_MAX_RUNS_PER_DAY


def test_limits_from_env_custom_prefix() -> None:
    env = {"PARSER_AI_MAX_RUNS_PER_DAY": "7"}
    limits = BudgetLimits.from_env(env, prefix="PARSER_AI_")
    assert limits.max_runs_per_day == 7


def test_report_under_limits_passes() -> None:
    report = BudgetReport(
        runs_in_last_24h=2,
        tokens_in_last_30d=1000,
        limits=BudgetLimits(max_runs_per_day=10, max_tokens_per_month=10_000),
    )
    assert not report.is_rate_limited
    assert not report.is_over_token_budget
    assert report.remaining_runs == 8
    assert report.remaining_tokens == 9000
    evaluate_budget(report)  # no raise


def test_report_at_rate_limit_raises() -> None:
    report = BudgetReport(
        runs_in_last_24h=10,
        tokens_in_last_30d=0,
        limits=BudgetLimits(max_runs_per_day=10, max_tokens_per_month=10_000),
    )
    assert report.is_rate_limited
    assert report.remaining_runs == 0
    with pytest.raises(BudgetExceededError) as exc_info:
        evaluate_budget(report)
    assert exc_info.value.limit_kind == "runs_per_day"
    assert exc_info.value.limit_value == 10
    assert exc_info.value.current_value == 10


def test_report_over_token_budget_raises() -> None:
    report = BudgetReport(
        runs_in_last_24h=0,
        tokens_in_last_30d=11_000,
        limits=BudgetLimits(max_runs_per_day=10, max_tokens_per_month=10_000),
    )
    assert report.is_over_token_budget
    assert report.remaining_tokens == 0
    with pytest.raises(BudgetExceededError) as exc_info:
        evaluate_budget(report)
    assert exc_info.value.limit_kind == "tokens_per_month"


def test_rate_limit_takes_priority_over_token_budget() -> None:
    """Если оба превышены — сообщение про rate limit (более actionable)."""
    report = BudgetReport(
        runs_in_last_24h=10,
        tokens_in_last_30d=11_000,
        limits=BudgetLimits(max_runs_per_day=10, max_tokens_per_month=10_000),
    )
    with pytest.raises(BudgetExceededError) as exc_info:
        evaluate_budget(report)
    assert exc_info.value.limit_kind == "runs_per_day"


def test_zero_limit_means_unlimited() -> None:
    report = BudgetReport(
        runs_in_last_24h=999,
        tokens_in_last_30d=999_999,
        limits=BudgetLimits(max_runs_per_day=0, max_tokens_per_month=0),
    )
    assert not report.is_rate_limited
    assert not report.is_over_token_budget
    assert report.remaining_runs == -1
    assert report.remaining_tokens == -1
    evaluate_budget(report)  # no raise
