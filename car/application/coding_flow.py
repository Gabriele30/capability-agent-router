"""Compose authorized coding execution with existing verified-failure handling."""

from __future__ import annotations

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
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.coding.base import CodingProvider
from car.coding.models import CodingExecutionPolicy, CodingTaskContext
from car.coding.verification import CodingVerificationCoordinator
from car.escalation.models import HandoffPolicy
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation
from car.router.models import Route
from car.telemetry import (
    AttemptCapability,
    ExecutionTelemetry,
    ExecutionTelemetryCollector,
    FinalOutcome,
    VerificationTelemetry,
)
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
    controlled_write: object | None = None
    outcome: CodingFlowOutcome
    telemetry: ExecutionTelemetry | None = None


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
    codex_model: str | None = None,
    patch_applier: SafePatchApplier | None = None,
    verification_coordinator: CodingVerificationCoordinator | None = None,
    telemetry_collector: ExecutionTelemetryCollector | None = None,
    controlled_write_pipeline: ControlledCodexWritePipeline | None = None,
) -> CodingFlowResult:
    """Run coding once; only verified failure evidence can enter the existing Codex path."""
    route = routing_evaluation.final_decision.route
    collector = telemetry_collector or ExecutionTelemetryCollector()
    collector.start_execution(
        initial_route=route,
        task_category=(
            routing_evaluation.final_decision.categories[0].value
            if routing_evaluation.final_decision.categories
            else None
        ),
    )
    if route == Route.CODEX:
        return _execute_direct_codex_controlled_write(
            repository_root=repository_root,
            coding_context=coding_context,
            verification_plan=verification_plan,
            codex_write_policy=codex_write_policy,
            codex_write_authorization=codex_write_authorization,
            codex_write_paths=codex_write_paths,
            codex_model=codex_model,
            controlled_write_pipeline=controlled_write_pipeline,
            collector=collector,
            route=route,
        )
    gemini_attempt = collector.start_attempt(
        AttemptCapability.GEMINI,
        provider="gemini",
        model=_provider_model(coding_provider),
    )
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
    verification = _verification_telemetry(coding)
    collector.finish_attempt(
        gemini_attempt,
        succeeded=coding.succeeded,
        failure_kind=(coding.failure_kind.value if coding.failure_kind else None),
        verification=verification,
        usage=(
            coding.pipeline_result.coding_attempt.usage
            if coding.pipeline_result and coding.pipeline_result.coding_attempt
            else None
        ),
    )
    if verification is not None:
        collector.record_verification(verification)
    if coding.succeeded:
        return _with_telemetry(
            CodingFlowResult(
                attempted=True,
                succeeded=True,
                coding=coding,
                outcome=CodingFlowOutcome.CODING_SUCCEEDED,
            ),
            collector,
            route,
            FinalOutcome.VERIFIED_SUCCESS,
            True,
        )
    if coding.failure_kind == CodingPipelineApplicationFailureKind.EXECUTION_DISABLED:
        return _with_telemetry(
            CodingFlowResult(
                attempted=False,
                succeeded=False,
                coding=coding,
                outcome=CodingFlowOutcome.CODING_EXECUTION_DISABLED,
            ),
            collector,
            route,
            FinalOutcome.UNCHANGED,
            False,
        )
    if coding.failure_kind == CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE:
        return _with_telemetry(
            CodingFlowResult(
                attempted=False,
                succeeded=False,
                coding=coding,
                outcome=CodingFlowOutcome.ROUTE_NOT_ELIGIBLE,
            ),
            collector,
            route,
            FinalOutcome.UNCHANGED,
            False,
        )
    pipeline = coding.pipeline_result
    if pipeline is None or pipeline.coding_attempt is None:
        return _with_telemetry(
            CodingFlowResult(
                attempted=coding.attempted,
                succeeded=False,
                coding=coding,
                outcome=CodingFlowOutcome.CODING_FAILED_NO_ESCALATION,
            ),
            collector,
            route,
            FinalOutcome.FAILED,
            False,
        )
    if route != Route.GEMINI_TO_CODEX and pipeline.verification is None:
        return _with_telemetry(
            CodingFlowResult(
                attempted=coding.attempted,
                succeeded=False,
                coding=coding,
                outcome=CodingFlowOutcome.CODING_FAILED_NO_ESCALATION,
            ),
            collector,
            route,
            FinalOutcome.FAILED,
            False,
        )
    if not _safe_for_codex_fallback(pipeline):
        return _with_telemetry(
            CodingFlowResult(
                attempted=coding.attempted,
                succeeded=False,
                coding=coding,
                outcome=(
                    CodingFlowOutcome.WORKSPACE_UNCERTAIN
                    if _pipeline_source_uncertain(pipeline)
                    else CodingFlowOutcome.CODING_FAILED_NO_ESCALATION
                ),
            ),
            collector,
            route,
            FinalOutcome.UNCERTAIN if _pipeline_source_uncertain(pipeline) else FinalOutcome.FAILED,
            False,
        )
    if route != Route.GEMINI_TO_CODEX:
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
            codex_model=codex_model,
            controlled_write_pipeline=controlled_write_pipeline,
        )
        return _with_telemetry(
            CodingFlowResult(
                attempted=coding.attempted,
                succeeded=False,
                coding=coding,
                post_failure=post_failure,
                outcome=CodingFlowOutcome.CODING_FAILED_NO_ESCALATION,
            ),
            collector,
            route,
            FinalOutcome.FAILED,
            False,
        )
    target = (
        AttemptCapability.CODEX_CONTROLLED_WRITE
        if codex_write_policy
        and codex_write_policy.enabled
        and codex_write_authorization
        and codex_write_authorization.authorized
        and codex_write_paths
        and verification_plan.commands
        else AttemptCapability.CODEX_READ_ONLY
    )
    collector.record_escalation(
        AttemptCapability.GEMINI,
        target,
        reason="verification_failed" if pipeline.verification else "safe_pipeline_failure",
    )
    codex_attempt = collector.start_attempt(target, provider="codex", model=codex_model)
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
        codex_model=codex_model,
        controlled_write_pipeline=controlled_write_pipeline,
    )
    collector.finish_attempt(
        codex_attempt,
        succeeded=_controlled_write_succeeded(post_failure),
        usage=_controlled_write_usage(post_failure),
    )
    source_state = getattr(getattr(post_failure, "controlled_write", None), "source_state", None)
    final_outcome = (
        FinalOutcome.VERIFIED_SUCCESS
        if _controlled_write_succeeded(post_failure)
        else (
            FinalOutcome.UNCERTAIN
            if source_state and source_state.value == "uncertain"
            else FinalOutcome.RESTORED
        )
    )
    return _with_telemetry(
        CodingFlowResult(
            attempted=coding.attempted,
            succeeded=_controlled_write_succeeded(post_failure),
            coding=coding,
            post_failure=post_failure,
            outcome=_outcome(post_failure),
        ),
        collector,
        route,
        final_outcome,
        _controlled_write_succeeded(post_failure),
        source_state,
    )


def _with_telemetry(result, collector, route, outcome, verified_success, source_state=None):
    """Fail open only for expected telemetry-contract failures."""
    try:
        return result.model_copy(
            update={
                "telemetry": collector.finish_execution(
                    final_route=route,
                    final_outcome=outcome,
                    verified_success=verified_success,
                    source_state=source_state,
                )
            }
        )
    except (RuntimeError, ValueError):
        return result


def _verification_telemetry(coding):
    verification = (
        getattr(coding.pipeline_result, "verification", None) if coding.pipeline_result else None
    )
    if verification is None:
        return None
    checks = verification.checks
    failures = sum(
        check.exit_code != 0 or check.executable_not_found or check.timed_out for check in checks
    )
    return VerificationTelemetry(
        attempted=verification.attempted,
        passed=verification.passed,
        check_count=len(checks),
        passed_check_count=len(checks) - failures,
        failed_check_count=failures,
        timeout_count=sum(check.timed_out for check in checks),
    )


def _execute_direct_codex_controlled_write(
    *,
    repository_root,
    coding_context,
    verification_plan,
    codex_write_policy,
    codex_write_authorization,
    codex_write_paths,
    codex_model,
    controlled_write_pipeline,
    collector,
    route,
) -> CodingFlowResult:
    """Execute an authoritative Codex route without creating a Gemini attempt."""
    attempt = collector.start_attempt(
        AttemptCapability.CODEX_CONTROLLED_WRITE,
        provider="codex",
        model=codex_model,
    )
    pipeline = controlled_write_pipeline or ControlledCodexWritePipeline()
    arguments = (
        repository_root,
        coding_context.task,
        codex_write_paths,
        verification_plan,
        codex_write_policy or CodexWritePolicy(),
        codex_write_authorization or CodexWriteAuthorization(),
    )
    controlled = (
        pipeline.execute(*arguments, codex_model=codex_model)
        if codex_model
        else pipeline.execute(*arguments)
    )
    verification = _controlled_verification_telemetry(controlled.verification_result)
    collector.finish_attempt(
        attempt,
        succeeded=controlled.accepted,
        failure_kind=controlled.failure_kind.value if controlled.failure_kind else None,
        usage=_controlled_result_usage(controlled),
        verification=verification,
    )
    if verification:
        collector.record_verification(verification)
    coding = CodingPipelineApplicationResult(
        attempted=controlled.attempted,
        succeeded=controlled.accepted,
        failure_kind=(
            None if controlled.accepted else CodingPipelineApplicationFailureKind.PIPELINE_FAILED
        ),
    )
    source_state = controlled.source_state
    return _with_telemetry(
        CodingFlowResult(
            attempted=controlled.attempted,
            succeeded=controlled.accepted,
            coding=coding,
            controlled_write=controlled,
            outcome=(
                CodingFlowOutcome.CODEX_CONTROLLED_WRITE_SUCCEEDED
                if controlled.accepted
                else CodingFlowOutcome.WORKSPACE_UNCERTAIN
                if source_state.value == "uncertain"
                else CodingFlowOutcome.CODEX_CONTROLLED_WRITE_FAILED
            ),
        ),
        collector,
        route,
        (
            FinalOutcome.VERIFIED_SUCCESS
            if controlled.accepted
            else FinalOutcome.UNCERTAIN
            if source_state.value == "uncertain"
            else FinalOutcome.RESTORED
        ),
        controlled.accepted,
        source_state,
    )


def _controlled_verification_telemetry(result) -> VerificationTelemetry | None:
    if result is None:
        return None
    verification = result.verification_result
    checks = getattr(verification, "checks", []) if verification else []
    return VerificationTelemetry(
        attempted=result.attempted,
        passed=result.accepted,
        check_count=len(checks),
        passed_check_count=len(checks) if result.accepted else 0,
        failed_check_count=0 if result.accepted else len(checks),
        timeout_count=sum(check.timed_out for check in checks),
    )


def _safe_for_codex_fallback(pipeline) -> bool:
    """Permit one fallback only when the Gemini attempt left source known safe."""
    verification = pipeline.verification
    if verification is not None:
        return (
            not verification.passed
            and verification.rollback_failure is None
            and verification.rolled_back
        )
    patch_apply = pipeline.patch_apply
    if patch_apply is None:
        return True
    return (
        not patch_apply.succeeded
        and patch_apply.rollback_failure_kind is None
        and (patch_apply.rolled_back or not patch_apply.changed_files)
    )


def _pipeline_source_uncertain(pipeline) -> bool:
    verification = pipeline.verification
    if verification is not None:
        return verification.rollback_failure is not None
    patch_apply = pipeline.patch_apply
    return bool(patch_apply and patch_apply.rollback_failure_kind is not None)


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


def _controlled_write_usage(post_failure: PostFailurePipelineResult):
    """Preserve structured runtime usage even when controlled changes are rejected."""
    return getattr(getattr(post_failure.controlled_write, "codex_result", None), "usage", None)


def _controlled_result_usage(controlled):
    return getattr(getattr(controlled, "codex_result", None), "usage", None)


def _provider_model(provider: CodingProvider) -> str | None:
    """Read an optional provider-neutral configured model identifier safely."""
    model = getattr(provider, "model", None)
    return model if isinstance(model, str) and model else None
