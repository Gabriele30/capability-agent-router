"""Privacy-safe results for internal benchmark strategy execution."""

from enum import StrEnum

from pydantic import BaseModel, field_validator

from car.benchmark.models import BenchmarkStrategy
from car.coding.models import normalize_repository_relative_path
from car.economics.models import ExecutionCost
from car.telemetry.models import ExecutionTelemetry, FinalOutcome


class BenchmarkFailureKind(StrEnum):
    TASK_FAILED = "task_failed"
    EXECUTION_FAILED = "execution_failed"
    INVARIANT_FAILED = "invariant_failed"


class BenchmarkInvariantError(RuntimeError):
    """Signals invalid benchmark setup without exposing task/provider failure."""


class BenchmarkExecutionOutcome(BaseModel):
    """Internal executor outcome with bounded failure metadata for benchmark export."""

    telemetry: ExecutionTelemetry
    rejected_paths: tuple[str, ...] = ()
    task_changed_paths: tuple[str, ...] = ()
    auxiliary_changed_paths: tuple[str, ...] = ()

    @field_validator("rejected_paths", "task_changed_paths", "auxiliary_changed_paths")
    @classmethod
    def rejected_paths_are_repository_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_repository_relative_path(value) for value in values)


class BenchmarkTaskResult(BaseModel):
    case_id: str
    strategy: BenchmarkStrategy
    verified_success: bool
    duration_ms: int
    attempt_count: int
    telemetry: ExecutionTelemetry | None = None
    reference_cost: ExecutionCost | None = None
    cost_complete: bool = False
    final_outcome: FinalOutcome | None = None
    source_state: str | None = None
    failure_kind: BenchmarkFailureKind | None = None
    failure_reason: str | None = None
    rejected_paths: tuple[str, ...] = ()
    task_changed_paths: tuple[str, ...] = ()
    auxiliary_changed_paths: tuple[str, ...] = ()

    @field_validator("rejected_paths", "task_changed_paths", "auxiliary_changed_paths")
    @classmethod
    def rejected_paths_are_repository_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_repository_relative_path(value) for value in values)
