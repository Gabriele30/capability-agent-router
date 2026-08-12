"""Fail-closed aggregation for privacy-safe benchmark task results."""

from statistics import mean, median

from pydantic import BaseModel, Field

from car.benchmark.models import BenchmarkRunMetadata, BenchmarkStrategy
from car.benchmark.results import BenchmarkTaskResult
from car.telemetry.models import AttemptCapability


class BenchmarkStrategySummary(BaseModel):
    strategy: BenchmarkStrategy
    task_count: int = Field(ge=0)
    verified_success_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
    cost_complete: bool
    total_reference_cost_usd: float | None = Field(default=None, ge=0)
    known_cost_count: int = Field(ge=0)
    unknown_cost_count: int = Field(ge=0)
    cost_per_verified_success_usd: float | None = Field(default=None, ge=0)
    mean_latency_ms: float = Field(ge=0)
    median_latency_ms: float = Field(ge=0)
    total_attempt_count: int = Field(ge=0)
    codex_escalation_count: int | None = Field(default=None, ge=0)
    codex_escalation_rate: float | None = Field(default=None, ge=0, le=1)
    codex_avoidance_count: int | None = Field(default=None, ge=0)
    codex_avoidance_rate: float | None = Field(default=None, ge=0, le=1)


class BenchmarkComparison(BaseModel):
    left_strategy: BenchmarkStrategy = BenchmarkStrategy.CAR
    right_strategy: BenchmarkStrategy = BenchmarkStrategy.CODEX_ONLY
    success_delta_percentage_points: float | None = None
    total_reference_cost_delta_percent: float | None = None
    cost_per_verified_success_delta_percent: float | None = None
    mean_latency_delta_percent: float | None = None


class BenchmarkReport(BaseModel):
    metadata: BenchmarkRunMetadata
    task_results: tuple[BenchmarkTaskResult, ...]
    summaries: tuple[BenchmarkStrategySummary, ...]
    comparison: BenchmarkComparison | None = None


def aggregate_benchmark(
    metadata: BenchmarkRunMetadata, results: tuple[BenchmarkTaskResult, ...]
) -> BenchmarkReport:
    if not results:
        raise ValueError("benchmark results must not be empty")
    summaries = tuple(
        _summarize(strategy, tuple(item for item in results if item.strategy == strategy))
        for strategy in metadata.strategies
    )
    by_strategy = {summary.strategy: summary for summary in summaries}
    comparison = (
        _compare(by_strategy[BenchmarkStrategy.CAR], by_strategy[BenchmarkStrategy.CODEX_ONLY])
        if BenchmarkStrategy.CAR in by_strategy and BenchmarkStrategy.CODEX_ONLY in by_strategy
        else None
    )
    return BenchmarkReport(
        metadata=metadata,
        task_results=results,
        summaries=summaries,
        comparison=comparison,
    )


def _summarize(
    strategy: BenchmarkStrategy, results: tuple[BenchmarkTaskResult, ...]
) -> BenchmarkStrategySummary:
    if not results:
        raise ValueError(f"benchmark strategy has no task results: {strategy.value}")
    task_count = len(results)
    successes = sum(item.verified_success for item in results)
    known_costs = [
        item.reference_cost.reference_inference_cost_usd
        for item in results
        if (
            item.cost_complete
            and item.reference_cost is not None
            and item.reference_cost.reference_inference_cost_usd is not None
        )
    ]
    cost_complete = len(known_costs) == task_count
    total_cost = sum(known_costs) if cost_complete else None
    codex_attempts = [
        bool(
            item.telemetry
            and (
                item.telemetry.escalated
                or any(
                    attempt.capability == AttemptCapability.CODEX_CONTROLLED_WRITE
                    for attempt in item.telemetry.attempts
                )
            )
        )
        for item in results
    ]
    is_car = strategy == BenchmarkStrategy.CAR
    return BenchmarkStrategySummary(
        strategy=strategy,
        task_count=task_count,
        verified_success_count=successes,
        failed_count=task_count - successes,
        success_rate=successes / task_count,
        cost_complete=cost_complete,
        total_reference_cost_usd=total_cost,
        known_cost_count=len(known_costs),
        unknown_cost_count=task_count - len(known_costs),
        cost_per_verified_success_usd=(
            total_cost / successes if total_cost is not None and successes else None
        ),
        mean_latency_ms=mean(item.duration_ms for item in results),
        median_latency_ms=median(item.duration_ms for item in results),
        total_attempt_count=sum(item.attempt_count for item in results),
        codex_escalation_count=sum(codex_attempts) if is_car else None,
        codex_escalation_rate=sum(codex_attempts) / task_count if is_car else None,
        codex_avoidance_count=task_count - sum(codex_attempts) if is_car else None,
        codex_avoidance_rate=(task_count - sum(codex_attempts)) / task_count if is_car else None,
    )


def _compare(car: BenchmarkStrategySummary, codex: BenchmarkStrategySummary) -> BenchmarkComparison:
    return BenchmarkComparison(
        success_delta_percentage_points=(car.success_rate - codex.success_rate) * 100,
        total_reference_cost_delta_percent=_percent_delta(
            car.total_reference_cost_usd if car.cost_complete else None,
            codex.total_reference_cost_usd if codex.cost_complete else None,
        ),
        cost_per_verified_success_delta_percent=_percent_delta(
            car.cost_per_verified_success_usd if car.cost_complete else None,
            codex.cost_per_verified_success_usd if codex.cost_complete else None,
        ),
        mean_latency_delta_percent=_percent_delta(car.mean_latency_ms, codex.mean_latency_ms),
    )


def _percent_delta(left: float | None, right: float | None) -> float | None:
    if left is None or right is None or right == 0:
        return None
    return (left - right) / right * 100
