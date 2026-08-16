"""Privacy-safe results for internal benchmark strategy execution."""

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from car.application.coding import CodingPipelineOutcome
from car.benchmark.models import BenchmarkStrategy
from car.coding.models import normalize_repository_relative_path
from car.economics.models import ExecutionCost
from car.patching.models import PatchApplyFailureKind, PatchViolationKind
from car.providers.models import ProviderErrorKind
from car.telemetry.models import ExecutionTelemetry, FinalOutcome


class BenchmarkFailureKind(StrEnum):
    TASK_FAILED = "task_failed"
    EXECUTION_FAILED = "execution_failed"
    INVARIANT_FAILED = "invariant_failed"


class BenchmarkInvariantError(RuntimeError):
    """Signals invalid benchmark setup without exposing task/provider failure."""


class BenchmarkPatchViolation(BaseModel):
    """Bounded, content-free diagnostic evidence from an existing patch validation result."""

    kind: PatchViolationKind
    path: str | None = None
    summary: str = Field(min_length=1, max_length=256)

    @field_validator("path")
    @classmethod
    def path_is_repository_relative(cls, value: str | None) -> str | None:
        return normalize_repository_relative_path(value) if value is not None else None

    @field_validator("summary")
    @classmethod
    def summary_is_bounded_single_line(cls, value: str) -> str:
        summary = " ".join(value.split())
        if not summary:
            raise ValueError("summary must not be blank")
        return summary[:256]


class BenchmarkExecutionOutcome(BaseModel):
    """Internal executor outcome with bounded failure metadata for benchmark export."""

    telemetry: ExecutionTelemetry
    patch_violations: tuple[BenchmarkPatchViolation, ...] = ()
    rejected_paths: tuple[str, ...] = ()
    task_changed_paths: tuple[str, ...] = ()
    auxiliary_changed_paths: tuple[str, ...] = ()
    pipeline_outcome: CodingPipelineOutcome | None = None
    patch_apply_failure_kind: PatchApplyFailureKind | None = None
    patch_apply_path: str | None = None
    provider_error_kind: ProviderErrorKind | None = None
    provider_http_status: int | None = Field(default=None, ge=100, le=599)
    provider_error_status: str | None = Field(default=None, max_length=64)
    provider_error_message: str | None = Field(default=None, max_length=500)

    @field_validator("rejected_paths", "task_changed_paths", "auxiliary_changed_paths")
    @classmethod
    def rejected_paths_are_repository_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_repository_relative_path(value) for value in values)

    @field_validator("patch_apply_path")
    @classmethod
    def patch_apply_path_is_repository_relative(cls, value: str | None) -> str | None:
        return normalize_repository_relative_path(value) if value is not None else None


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
    patch_violations: tuple[BenchmarkPatchViolation, ...] = ()
    rejected_paths: tuple[str, ...] = ()
    task_changed_paths: tuple[str, ...] = ()
    auxiliary_changed_paths: tuple[str, ...] = ()
    pipeline_outcome: CodingPipelineOutcome | None = None
    patch_apply_failure_kind: PatchApplyFailureKind | None = None
    patch_apply_path: str | None = None
    provider_error_kind: ProviderErrorKind | None = None
    provider_http_status: int | None = Field(default=None, ge=100, le=599)
    provider_error_status: str | None = Field(default=None, max_length=64)
    provider_error_message: str | None = Field(default=None, max_length=500)

    @field_validator("rejected_paths", "task_changed_paths", "auxiliary_changed_paths")
    @classmethod
    def rejected_paths_are_repository_relative(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(normalize_repository_relative_path(value) for value in values)

    @field_validator("patch_apply_path")
    @classmethod
    def patch_apply_path_is_repository_relative(cls, value: str | None) -> str | None:
        return normalize_repository_relative_path(value) if value is not None else None
