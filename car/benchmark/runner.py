"""Injected, benchmark-only strategy runner over B3-A isolated workspaces."""

from pathlib import Path
from time import monotonic
from typing import Protocol

from car.benchmark.models import BenchmarkCase, BenchmarkStrategy
from car.benchmark.results import (
    BenchmarkExecutionOutcome,
    BenchmarkFailureKind,
    BenchmarkInvariantError,
    BenchmarkTaskResult,
)
from car.benchmark.workspace import BenchmarkWorkspaceSet
from car.economics.pricing import ReferenceCostCalculator
from car.telemetry.models import ExecutionTelemetry, FinalOutcome


class BenchmarkExecutor(Protocol):
    def execute(
        self, case: BenchmarkCase, workspace: Path, strategy: BenchmarkStrategy
    ) -> ExecutionTelemetry | BenchmarkExecutionOutcome: ...


class BenchmarkRunner:
    """Coordinates injected strategy execution without changing production routing."""

    def __init__(self, executor: BenchmarkExecutor) -> None:
        self._executor = executor
        self._costs = ReferenceCostCalculator()

    def run_case(
        self,
        case: BenchmarkCase,
        fixture: Path,
        strategies: tuple[BenchmarkStrategy, ...] = tuple(BenchmarkStrategy),
    ) -> tuple[BenchmarkTaskResult, ...]:
        if not strategies:
            raise ValueError("at least one benchmark strategy is required")
        source_identity = BenchmarkWorkspaceSet.identity(fixture)
        spaces = BenchmarkWorkspaceSet(fixture)
        try:
            results = tuple(
                self._run(case, strategy, spaces.workspaces[strategy]) for strategy in strategies
            )
            if BenchmarkWorkspaceSet.identity(fixture) != source_identity:
                raise RuntimeError("benchmark source fixture changed during execution")
            return results
        finally:
            spaces.cleanup()

    def _run(
        self, case: BenchmarkCase, strategy: BenchmarkStrategy, workspace: Path
    ) -> BenchmarkTaskResult:
        started = monotonic()
        try:
            execute_outcome = getattr(self._executor, "execute_outcome", None)
            outcome = (
                execute_outcome(case, workspace, strategy)
                if callable(execute_outcome)
                else self._executor.execute(case, workspace, strategy)
            )
            if isinstance(outcome, BenchmarkExecutionOutcome):
                telemetry = outcome.telemetry
                rejected_paths = outcome.rejected_paths
                patch_violations = outcome.patch_violations
                task_changed_paths = outcome.task_changed_paths
                auxiliary_changed_paths = outcome.auxiliary_changed_paths
                pipeline_outcome = outcome.pipeline_outcome
                provider_error_kind = outcome.provider_error_kind
            else:
                telemetry = outcome
                rejected_paths = ()
                patch_violations = ()
                task_changed_paths = ()
                auxiliary_changed_paths = ()
                pipeline_outcome = None
                provider_error_kind = None
            attempt_costs = tuple(
                self._costs.calculate(provider=item.provider, model=item.model, usage=item.usage)
                for item in telemetry.attempts
            )
            cost = self._costs.aggregate(attempt_costs)
            return BenchmarkTaskResult(
                case_id=case.id,
                strategy=strategy,
                verified_success=telemetry.verified_success is True,
                duration_ms=round((monotonic() - started) * 1000),
                attempt_count=len(telemetry.attempts),
                telemetry=telemetry,
                reference_cost=cost,
                cost_complete=cost.complete,
                final_outcome=telemetry.final_outcome,
                source_state=telemetry.source_state.value if telemetry.source_state else None,
                failure_kind=(
                    None if telemetry.verified_success is True else BenchmarkFailureKind.TASK_FAILED
                ),
                failure_reason=(
                    None
                    if telemetry.verified_success is True
                    else "strategy did not achieve verified success"
                ),
                rejected_paths=rejected_paths,
                patch_violations=patch_violations,
                task_changed_paths=task_changed_paths,
                auxiliary_changed_paths=auxiliary_changed_paths,
                pipeline_outcome=pipeline_outcome,
                provider_error_kind=provider_error_kind,
            )
        except BenchmarkInvariantError:
            return BenchmarkTaskResult(
                case_id=case.id,
                strategy=strategy,
                verified_success=False,
                duration_ms=round((monotonic() - started) * 1000),
                attempt_count=0,
                cost_complete=False,
                final_outcome=FinalOutcome.FAILED,
                failure_kind=BenchmarkFailureKind.INVARIANT_FAILED,
                failure_reason="invalid benchmark execution context",
            )
        except Exception:
            return BenchmarkTaskResult(
                case_id=case.id,
                strategy=strategy,
                verified_success=False,
                duration_ms=round((monotonic() - started) * 1000),
                attempt_count=0,
                cost_complete=False,
                final_outcome=FinalOutcome.FAILED,
                failure_kind=BenchmarkFailureKind.EXECUTION_FAILED,
                failure_reason="strategy executor raised an exception",
            )
