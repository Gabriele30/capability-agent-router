"""Pure human-readable presentation mapping for explicit coding execution results."""

from dataclasses import dataclass, field

from car.application.coding import CodingPipelineOutcome
from car.application.coding_execution import CodingPipelineApplicationFailureKind
from car.application.coding_flow import CodingFlowOutcome
from car.application.execution_gateway import CodingFlowGatewayFailureKind, CodingFlowGatewayResult
from car.coding.verification import CodingVerificationFailureKind


@dataclass(frozen=True)
class ExecutionPresentation:
    route: str
    coding: str
    files_changed: int = 0
    temporary_changes: bool = False
    verification: str = "not run"
    verification_checks: list[str] = field(default_factory=list)
    rollback: str = "not required"
    codex_analysis: str = "not required"
    workspace: str = "unchanged"
    task: str = "UNRESOLVED"
    failure_reason: str | None = None


def present_execution_result(result: CodingFlowGatewayResult) -> ExecutionPresentation:
    """Derive display state exclusively from the structured execution result."""
    if result.flow_result is None:
        return ExecutionPresentation(
            route="unknown",
            coding="not authorized",
            failure_reason=_gateway_reason(result.failure_kind),
        )
    flow = result.flow_result
    application = flow.coding
    pipeline = application.pipeline_result
    route = pipeline.route.value.upper() if pipeline else "unknown"
    if result.succeeded:
        changed = (
            len(pipeline.patch_apply.changed_files) if pipeline and pipeline.patch_apply else 0
        )
        return ExecutionPresentation(
            route=route,
            coding="verified",
            files_changed=changed,
            verification="passed",
            verification_checks=_checks(pipeline),
            workspace="updated safely",
            task="RESOLVED",
        )
    if pipeline is None:
        return ExecutionPresentation(
            route=route,
            coding=_application_label(application.failure_kind),
            failure_reason=_application_reason(application.failure_kind),
        )

    verification = pipeline.verification
    apply = pipeline.patch_apply
    rollback = "not required"
    workspace = "unchanged"
    temporary_changes = False
    if verification is not None:
        if verification.rollback_failure or not verification.rolled_back:
            rollback = "failed"
            workspace = "uncertain"
        elif verification.rolled_back:
            rollback = "succeeded"
            workspace = "restored"
            temporary_changes = bool(apply and apply.changed_files)
    elif apply is not None and apply.rolled_back:
        rollback = "succeeded"
        workspace = "restored"
        temporary_changes = bool(apply.changed_files)

    return ExecutionPresentation(
        route=route,
        coding=_pipeline_label(pipeline.outcome),
        files_changed=len(apply.changed_files) if apply else 0,
        temporary_changes=temporary_changes,
        verification=_verification_label(verification),
        verification_checks=_checks(pipeline),
        rollback=rollback,
        codex_analysis=_codex_label(flow.outcome),
        workspace=workspace,
        failure_reason=_failure_reason(pipeline, verification, flow.outcome),
    )


def _checks(pipeline) -> list[str]:
    if pipeline is None or pipeline.verification is None:
        return []
    output = []
    for check in pipeline.verification.checks:
        name = (
            "pytest"
            if check.command.args[:3] == ["python", "-m", "pytest"]
            else check.command.args[0]
        )
        status = "timeout" if check.timed_out else "PASS" if check.exit_code == 0 else "FAIL"
        detail = f"exit {check.exit_code}" if check.exit_code is not None else status
        output.append(f"{name}: {status} ({detail})")
    return output


def _pipeline_label(outcome: CodingPipelineOutcome) -> str:
    labels = {
        CodingPipelineOutcome.CODING_PROVIDER_UNAVAILABLE: "provider unavailable",
        CodingPipelineOutcome.CODING_PROVIDER_FAILED: "provider failed",
        CodingPipelineOutcome.PATCH_VALIDATION_FAILED: "patch validation failed",
        CodingPipelineOutcome.PATCH_APPLY_FAILED: "patch apply failed",
        CodingPipelineOutcome.VERIFICATION_FAILED: "verification failed",
    }
    return labels.get(outcome, outcome.value.replace("_", " "))


def _verification_label(verification) -> str:
    if verification is None:
        return "not run"
    if verification.passed:
        return "passed"
    if verification.failure_kind == CodingVerificationFailureKind.CHECK_TIMEOUT:
        return "timeout"
    return "failed"


def _codex_label(outcome: CodingFlowOutcome) -> str:
    if outcome == CodingFlowOutcome.CODEX_ANALYSIS_SUCCEEDED:
        return "succeeded (read-only)"
    if outcome == CodingFlowOutcome.CODEX_ANALYSIS_FAILED:
        return "failed (read-only)"
    if outcome == CodingFlowOutcome.CODEX_EXECUTION_DISABLED:
        return "disabled"
    if outcome == CodingFlowOutcome.WORKSPACE_UNCERTAIN:
        return "blocked (workspace uncertain)"
    return "not required"


def _failure_reason(pipeline, verification, flow_outcome: CodingFlowOutcome) -> str:
    if flow_outcome == CodingFlowOutcome.WORKSPACE_UNCERTAIN:
        return "Workspace state uncertain"
    if verification and verification.rollback_failure:
        return "Rollback failed"
    if verification and verification.failure_kind == CodingVerificationFailureKind.CHECK_TIMEOUT:
        return "Verification timed out"
    labels = {
        CodingPipelineOutcome.CODING_PROVIDER_UNAVAILABLE: "Provider unavailable",
        CodingPipelineOutcome.CODING_PROVIDER_FAILED: "Provider request failed",
        CodingPipelineOutcome.PATCH_VALIDATION_FAILED: "Patch validation failed",
        CodingPipelineOutcome.PATCH_APPLY_FAILED: "Patch apply failed",
        CodingPipelineOutcome.VERIFICATION_FAILED: "Verification failed",
    }
    return labels.get(pipeline.outcome, "Coding execution failed")


def _application_label(kind: CodingPipelineApplicationFailureKind | None) -> str:
    if kind == CodingPipelineApplicationFailureKind.EXECUTION_DISABLED:
        return "execution disabled"
    if kind == CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE:
        return "route not eligible"
    return "execution failed"


def _application_reason(kind: CodingPipelineApplicationFailureKind | None) -> str:
    if kind == CodingPipelineApplicationFailureKind.EXECUTION_DISABLED:
        return "Coding execution disabled"
    if kind == CodingPipelineApplicationFailureKind.ROUTE_NOT_ELIGIBLE:
        return "Route is not eligible for coding execution"
    return "Coding execution failed"


def _gateway_reason(kind: CodingFlowGatewayFailureKind | None) -> str:
    if kind == CodingFlowGatewayFailureKind.NOT_AUTHORIZED:
        return "Execution not authorized"
    return "Coding execution failed"
