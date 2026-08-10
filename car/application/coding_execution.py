"""Explicit fail-closed application boundary for internal coding execution."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from car.application.coding import (
    CodingPipelineOutcome,
    CodingPipelineResult,
    execute_coding_pipeline,
)
from car.coding.base import CodingProvider
from car.coding.models import CodingExecutionPolicy, CodingTaskContext
from car.coding.verification import CodingVerificationCoordinator
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.router.consultation import RoutingEvaluation
from car.verification.models import VerificationPlan


class CodingPipelineApplicationFailureKind(StrEnum):
    EXECUTION_DISABLED = "execution_disabled"
    ROUTE_NOT_ELIGIBLE = "route_not_eligible"
    PIPELINE_FAILED = "pipeline_failed"
    INVALID_REQUEST = "invalid_request"


class CodingPipelineExecutionPolicy(BaseModel):
    """Explicit authorization for the internal coding pipeline; disabled by default."""

    enabled: bool = False


class CodingPipelineApplicationResult(BaseModel):
    """Application-level execution status retaining the complete internal result."""

    attempted: bool
    succeeded: bool
    pipeline_result: CodingPipelineResult | None = None
    failure_kind: CodingPipelineApplicationFailureKind | None = None


def execute_authorized_coding_pipeline(
    *,
    repository_root: Path,
    routing_evaluation: RoutingEvaluation,
    coding_context: CodingTaskContext,
    coding_provider: CodingProvider,
    coding_policy: CodingExecutionPolicy | None,
    patch_validation_policy: PatchValidationPolicy | None,
    verification_plan: VerificationPlan,
    execution_policy: CodingPipelineExecutionPolicy | None = None,
    patch_applier: SafePatchApplier | None = None,
    verification_coordinator: CodingVerificationCoordinator | None = None,
) -> CodingPipelineApplicationResult:
    """Delegate one explicitly authorized request without adding recovery or fallback."""
    active_policy = execution_policy or CodingPipelineExecutionPolicy()
    if not active_policy.enabled:
        return CodingPipelineApplicationResult(
            attempted=False,
            succeeded=False,
            failure_kind=CodingPipelineApplicationFailureKind.EXECUTION_DISABLED,
        )

    pipeline_result = execute_coding_pipeline(
        repository_root=repository_root,
        routing_evaluation=routing_evaluation,
        coding_context=coding_context,
        coding_provider=coding_provider,
        coding_policy=coding_policy,
        patch_validation_policy=patch_validation_policy,
        verification_plan=verification_plan,
        patch_applier=patch_applier,
        verification_coordinator=verification_coordinator,
    )
    if pipeline_result.succeeded:
        return CodingPipelineApplicationResult(
            attempted=True,
            succeeded=True,
            pipeline_result=pipeline_result,
        )
    return CodingPipelineApplicationResult(
        attempted=pipeline_result.attempted,
        succeeded=False,
        pipeline_result=pipeline_result,
        failure_kind=(
            CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE
            if pipeline_result.outcome == CodingPipelineOutcome.ROUTE_NOT_ELIGIBLE
            else CodingPipelineApplicationFailureKind.PIPELINE_FAILED
        ),
    )
