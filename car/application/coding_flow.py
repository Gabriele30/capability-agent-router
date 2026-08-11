"""Compose authorized coding execution with existing verified-failure handling."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import (
    CodingPipelineApplicationFailureKind,
    CodingPipelineApplicationResult,
    CodingPipelineExecutionPolicy,
    execute_authorized_coding_pipeline,
)
from car.application.post_failure import (
    PostFailurePipelineOutcome,
    PostFailurePipelineResult,
    process_verified_coding_outcome,
)
from car.codex.runtime import CodexRuntime
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.coding.base import CodingProvider
from car.coding.models import CodingExecutionPolicy, CodingTaskContext
from car.coding.verification import CodingVerificationCoordinator
from car.escalation.models import HandoffPolicy
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation
from car.verification.models import VerificationPlan


class CodingFlowOutcome(StrEnum):
    CODING_EXECUTION_DISABLED = "coding_execution_disabled"
    ROUTE_NOT_ELIGIBLE = "route_not_eligible"
    CODING_SUCCEEDED = "coding_succeeded"
    CODING_FAILED_NO_ESCALATION = "coding_failed_no_escalation"
    CODEX_EXECUTION_DISABLED = "codex_execution_disabled"
    CODEX_ANALYSIS_SUCCEEDED = "codex_analysis_succeeded"
    CODEX_ANALYSIS_FAILED = "codex_analysis_failed"
    CODEX_CONTROLLED_WRITE_SUCCEEDED = "codex_controlled_write_succeeded"
    CODEX_CONTROLLED_WRITE_FAILED = "codex_controlled_write_failed"
    WORKSPACE_UNCERTAIN = "workspace_uncertain"


class CodingFlowResult(BaseModel):
    """Structured, in-memory result of one authorized coding and diagnostic flow."""

    attempted: bool
    succeeded: bool
    coding: CodingPipelineApplicationResult
    post_failure: PostFailurePipelineResult | None = None
    outcome: CodingFlowOutcome


def execute_coding_flow(
    *,
    repository_root: Path,
    routing_evaluation: RoutingEvaluation,
    repository_state: RepositoryState,
    coding_context: CodingTaskContext,
    coding_provider: CodingProvider,
    coding_policy: CodingExecutionPolicy | None,
    patch_validation_policy: PatchValidationPolicy | None,
    verification_plan: VerificationPlan,
    coding_execution_policy: CodingPipelineExecutionPolicy | None,
    handoff_policy: HandoffPolicy | None,
    codex_runtime: CodexRuntime,
    codex_execution_policy: CodexExecutionPolicy | None,
    codex_write_policy: CodexWritePolicy | None = None,
    codex_write_authorization: CodexWriteAuthorization | None = None,
    codex_write_paths: tuple[str, ...] = (),
    patch_applier: SafePatchApplier | None = None,
    verification_coordinator: CodingVerificationCoordinator | None = None,
) -> CodingFlowResult:
    """Run coding once; only verified failure evidence can enter the existing Codex path."""
    coding = execute_authorized_coding_pipeline(
        repository_root=repository_root,
        routing_evaluation=routing_evaluation,
        coding_context=coding_context,
        coding_provider=coding_provider,
        coding_policy=coding_policy,
        patch_validation_policy=patch_validation_policy,
        verification_plan=verification_plan,
        execution_policy=coding_execution_policy,
        patch_applier=patch_applier,
        verification_coordinator=verification_coordinator,
    )
    if coding.succeeded:
        return CodingFlowResult(
            attempted=True,
            succeeded=True,
            coding=coding,
            outcome=CodingFlowOutcome.CODING_SUCCEEDED,
        )
    if coding.failure_kind == CodingPipelineApplicationFailureKind.EXECUTION_DISABLED:
        return CodingFlowResult(
            attempted=False,
            succeeded=False,
            coding=coding,
            outcome=CodingFlowOutcome.CODING_EXECUTION_DISABLED,
        )
    if coding.failure_kind == CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE:
        return CodingFlowResult(
            attempted=False,
            succeeded=False,
            coding=coding,
            outcome=CodingFlowOutcome.ROUTE_NOT_ELIGIBLE,
        )
    pipeline = coding.pipeline_result
    if pipeline is None or pipeline.coding_attempt is None or pipeline.verification is None:
        return CodingFlowResult(
            attempted=coding.attempted,
            succeeded=False,
            coding=coding,
            outcome=CodingFlowOutcome.CODING_FAILED_NO_ESCALATION,
        )
    post_failure = process_verified_coding_outcome(
        task=coding_context.task,
        routing_evaluation=routing_evaluation,
        repository_state=repository_state,
        coding_context=coding_context,
        coding_attempt=pipeline.coding_attempt,
        patch_validation=pipeline.patch_validation,
        patch_apply=pipeline.patch_apply,
        verification=pipeline.verification,
        codex_runtime=codex_runtime,
        codex_execution_policy=codex_execution_policy,
        handoff_policy=handoff_policy,
        verification_plan=verification_plan,
        codex_write_policy=codex_write_policy or CodexWritePolicy(),
        codex_write_authorization=codex_write_authorization or CodexWriteAuthorization(),
        codex_write_paths=codex_write_paths,
    )
    return CodingFlowResult(
        attempted=coding.attempted,
        succeeded=_controlled_write_succeeded(post_failure),
        coding=coding,
        post_failure=post_failure,
        outcome=_outcome(post_failure),
    )


def _outcome(post_failure: PostFailurePipelineResult) -> CodingFlowOutcome:
    if post_failure.outcome == PostFailurePipelineOutcome.WORKSPACE_UNCERTAIN:
        return CodingFlowOutcome.WORKSPACE_UNCERTAIN
    if post_failure.outcome == PostFailurePipelineOutcome.CODEX_EXECUTION_DISABLED:
        return CodingFlowOutcome.CODEX_EXECUTION_DISABLED
    if post_failure.outcome == PostFailurePipelineOutcome.CODEX_EXECUTION_SUCCEEDED:
        return CodingFlowOutcome.CODEX_ANALYSIS_SUCCEEDED
    if post_failure.outcome == PostFailurePipelineOutcome.CODEX_CONTROLLED_WRITE_SUCCEEDED:
        return CodingFlowOutcome.CODEX_CONTROLLED_WRITE_SUCCEEDED
    if post_failure.outcome == PostFailurePipelineOutcome.CODEX_CONTROLLED_WRITE_FAILED:
        return CodingFlowOutcome.CODEX_CONTROLLED_WRITE_FAILED
    return CodingFlowOutcome.CODEX_ANALYSIS_FAILED


def _controlled_write_succeeded(post_failure: PostFailurePipelineResult) -> bool:
    """Only accepted controlled source changes can resolve the coding task."""
    return post_failure.selected_codex_mode == "controlled_write" and post_failure.succeeded
