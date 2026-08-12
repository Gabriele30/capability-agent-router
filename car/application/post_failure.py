"""Compose existing verified-failure evidence into one read-only Codex flow."""

from enum import StrEnum

from pydantic import BaseModel

from car.application.codex import CodexExecutionPolicy
from car.application.escalation import (
    CodexEscalationExecutionFailureKind,
    CodexEscalationExecutionResult,
    execute_codex_escalation,
)
from car.codex.runtime import CodexRuntime
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.codex_write.pipeline import ControlledCodexWritePipeline
from car.coding.models import CodingAttemptResult, CodingTaskContext
from car.coding.verification import CodingVerificationResult
from car.escalation.handoff import build_codex_handoff, decide_escalation
from car.escalation.models import CodexHandoff, EscalationDecision, EscalationReason, HandoffPolicy
from car.patching.models import PatchApplyResult, PatchValidationResult
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation
from car.verification.models import VerificationPlan


class PostFailurePipelineOutcome(StrEnum):
    NO_ESCALATION_REQUIRED = "no_escalation_required"
    ESCALATION_NOT_ALLOWED = "escalation_not_allowed"
    WORKSPACE_UNCERTAIN = "workspace_uncertain"
    CODEX_EXECUTION_DISABLED = "codex_execution_disabled"
    CODEX_EXECUTION_SUCCEEDED = "codex_execution_succeeded"
    CODEX_EXECUTION_FAILED = "codex_execution_failed"
    CODEX_CONTROLLED_WRITE_SUCCEEDED = "codex_controlled_write_succeeded"
    CODEX_CONTROLLED_WRITE_FAILED = "codex_controlled_write_failed"


class PostFailurePipelineResult(BaseModel):
    escalation: EscalationDecision
    handoff: CodexHandoff | None = None
    codex_execution: CodexEscalationExecutionResult | None = None
    controlled_write: object | None = None
    selected_codex_mode: str = "none"
    attempted_codex: bool
    succeeded: bool
    outcome: PostFailurePipelineOutcome


def process_verified_coding_outcome(
    *,
    task: str,
    routing_evaluation: RoutingEvaluation,
    repository_state: RepositoryState,
    coding_context: CodingTaskContext,
    coding_attempt: CodingAttemptResult,
    patch_validation: PatchValidationResult | None,
    patch_apply: PatchApplyResult | None,
    verification: CodingVerificationResult | None,
    codex_runtime: CodexRuntime,
    codex_execution_policy: CodexExecutionPolicy | None = None,
    handoff_policy: HandoffPolicy | None = None,
    verification_plan: VerificationPlan | None = None,
    codex_write_policy: CodexWritePolicy | None = None,
    codex_write_authorization: CodexWriteAuthorization | None = None,
    codex_write_paths: tuple[str, ...] = (),
    codex_model: str | None = None,
    codex_reasoning_effort=None,
    controlled_write_pipeline: ControlledCodexWritePipeline | None = None,
) -> PostFailurePipelineResult:
    """Use existing evidence, handoff, decision, and coordinator boundaries once."""
    handoff = build_codex_handoff(
        task,
        routing_evaluation,
        repository_state,
        coding_context,
        coding_attempt,
        patch_validation,
        patch_apply,
        verification,
        verification_plan,
        handoff_policy,
    )
    if verification is None and not _safe_without_verification(patch_apply):
        return PostFailurePipelineResult(
            escalation=EscalationDecision(
                should_escalate=False,
                reason=EscalationReason.WORKSPACE_STATE_UNCERTAIN,
            ),
            handoff=handoff,
            attempted_codex=False,
            succeeded=False,
            outcome=PostFailurePipelineOutcome.WORKSPACE_UNCERTAIN,
        )
    escalation = decide_escalation(
        handoff, verification_passed=bool(verification and verification.passed)
    )
    if verification and verification.passed:
        return PostFailurePipelineResult(
            escalation=escalation,
            attempted_codex=False,
            succeeded=True,
            outcome=PostFailurePipelineOutcome.NO_ESCALATION_REQUIRED,
        )
    if not escalation.should_escalate:
        return PostFailurePipelineResult(
            escalation=escalation,
            handoff=handoff,
            attempted_codex=False,
            succeeded=False,
            outcome=(
                PostFailurePipelineOutcome.WORKSPACE_UNCERTAIN
                if escalation.reason == EscalationReason.WORKSPACE_STATE_UNCERTAIN
                else PostFailurePipelineOutcome.ESCALATION_NOT_ALLOWED
            ),
        )
    write_policy = codex_write_policy or CodexWritePolicy()
    write_authorization = codex_write_authorization or CodexWriteAuthorization()
    write_eligible = (
        write_policy.enabled
        and write_authorization.authorized
        and bool(codex_write_paths)
        and verification_plan is not None
        and bool(verification_plan.commands)
    )
    if write_eligible:
        pipeline = controlled_write_pipeline or ControlledCodexWritePipeline()
        arguments = (
            repository_state.root,
            task,
            codex_write_paths,
            verification_plan,
            write_policy,
            write_authorization,
            handoff,
        )
        if codex_reasoning_effort is not None:
            controlled = pipeline.execute(
                *arguments,
                codex_model=codex_model,
                codex_reasoning_effort=codex_reasoning_effort,
            )
        elif codex_model:
            controlled = pipeline.execute(*arguments, codex_model=codex_model)
        else:
            controlled = pipeline.execute(*arguments)
        return PostFailurePipelineResult(
            escalation=escalation,
            handoff=handoff,
            controlled_write=controlled,
            attempted_codex=controlled.attempted,
            succeeded=controlled.accepted,
            selected_codex_mode="controlled_write",
            outcome=(
                PostFailurePipelineOutcome.CODEX_CONTROLLED_WRITE_SUCCEEDED
                if controlled.accepted
                else PostFailurePipelineOutcome.CODEX_CONTROLLED_WRITE_FAILED
            ),
        )
    codex_execution = execute_codex_escalation(
        repository_state.root,
        escalation,
        handoff,
        codex_runtime,
        codex_execution_policy,
    )
    return PostFailurePipelineResult(
        escalation=escalation,
        handoff=handoff,
        codex_execution=codex_execution,
        attempted_codex=codex_execution.attempted,
        succeeded=codex_execution.succeeded,
        selected_codex_mode="read_only",
        outcome=_outcome(codex_execution),
    )


def _outcome(result: CodexEscalationExecutionResult) -> PostFailurePipelineOutcome:
    if result.failure_kind == CodexEscalationExecutionFailureKind.EXECUTION_DISABLED:
        return PostFailurePipelineOutcome.CODEX_EXECUTION_DISABLED
    if result.succeeded:
        return PostFailurePipelineOutcome.CODEX_EXECUTION_SUCCEEDED
    return PostFailurePipelineOutcome.CODEX_EXECUTION_FAILED


def _safe_without_verification(patch_apply: PatchApplyResult | None) -> bool:
    """A pre-verification failure may escalate only when no source write is uncertain."""
    if patch_apply is None:
        return True
    return (
        not patch_apply.succeeded
        and patch_apply.rollback_failure_kind is None
        and (patch_apply.rolled_back or not patch_apply.changed_files)
    )
