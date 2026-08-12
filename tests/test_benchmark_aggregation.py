from datetime import UTC, datetime

import pytest

from car.benchmark.aggregation import aggregate_benchmark
from car.benchmark.models import BenchmarkRunMetadata, BenchmarkStrategy
from car.benchmark.results import BenchmarkTaskResult
from car.economics.models import ExecutionCost
from car.telemetry.models import FinalOutcome


def _metadata(strategies=(BenchmarkStrategy.GEMINI_ONLY,)) -> BenchmarkRunMetadata:
    return BenchmarkRunMetadata(
        run_id="run-1",
        manifest_hash="a" * 64,
        car_version="0.6.0",
        started_at=datetime.now(UTC),
        strategies=strategies,
        price_catalog_version="2026-08-11",
        price_catalog_verified_on="2026-08-11",
        cost_basis="public_api_list_price",
    )


def _result(
    strategy=BenchmarkStrategy.GEMINI_ONLY,
    *,
    success=True,
    duration=10,
    cost: float | None = 1.0,
    attempts=1,
    escalated=False,
) -> BenchmarkTaskResult:
    from car.telemetry.models import ExecutionTelemetry

    telemetry = ExecutionTelemetry(
        execution_id=f"{strategy.value}-{duration}",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_ms=duration,
        escalated=escalated,
        final_outcome=FinalOutcome.VERIFIED_SUCCESS if success else FinalOutcome.RESTORED,
        verified_success=success,
    )
    return BenchmarkTaskResult(
        case_id=f"case-{duration}",
        strategy=strategy,
        verified_success=success,
        duration_ms=duration,
        attempt_count=attempts,
        telemetry=telemetry,
        reference_cost=ExecutionCost(complete=cost is not None, reference_inference_cost_usd=cost),
        cost_complete=cost is not None,
        final_outcome=telemetry.final_outcome,
    )


def test_complete_aggregation_counts_failures_and_failed_costs():
    report = aggregate_benchmark(
        _metadata(),
        (
            _result(success=True, duration=10, cost=1.0),
            _result(success=False, duration=30, cost=3.0),
        ),
    )
    summary = report.summaries[0]

    assert summary.task_count == 2
    assert summary.verified_success_count == 1
    assert summary.failed_count == 1
    assert summary.success_rate == 0.5
    assert summary.total_reference_cost_usd == 4.0
    assert summary.cost_per_verified_success_usd == 4.0
    assert summary.mean_latency_ms == 20
    assert summary.median_latency_ms == 20
    assert summary.total_attempt_count == 2


def test_incomplete_cost_and_zero_success_are_never_zero_cost_or_division():
    report = aggregate_benchmark(
        _metadata(),
        (
            _result(success=False, duration=10, cost=None),
            _result(success=False, duration=20, cost=2.0),
        ),
    )
    summary = report.summaries[0]

    assert summary.cost_complete is False
    assert summary.total_reference_cost_usd is None
    assert summary.cost_per_verified_success_usd is None
    assert summary.known_cost_count == 1
    assert summary.unknown_cost_count == 1


def test_car_codex_metrics_and_comparison_fail_closed():
    report = aggregate_benchmark(
        _metadata((BenchmarkStrategy.CAR, BenchmarkStrategy.CODEX_ONLY)),
        (
            _result(BenchmarkStrategy.CAR, cost=1.0, escalated=False),
            _result(BenchmarkStrategy.CAR, cost=None, escalated=True),
            _result(BenchmarkStrategy.CODEX_ONLY, cost=None),
            _result(BenchmarkStrategy.CODEX_ONLY, success=False, cost=None),
        ),
    )
    car = report.summaries[0]

    assert car.codex_escalation_count == 1
    assert car.codex_escalation_rate == 0.5
    assert car.codex_avoidance_count == 1
    assert car.codex_avoidance_rate == 0.5
    assert report.comparison.success_delta_percentage_points == 50
    assert report.comparison.total_reference_cost_delta_percent is None
    assert report.comparison.cost_per_verified_success_delta_percent is None


def test_empty_result_set_is_rejected():
    with pytest.raises(ValueError, match="must not be empty"):
        aggregate_benchmark(_metadata(), ())
