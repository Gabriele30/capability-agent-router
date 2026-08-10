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


class PostFailurePipelineResult(BaseModel):
    escalation: EscalationDecision
    handoff: CodexHandoff | None = None
    codex_execution: CodexEscalationExecutionResult | None = None
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
    verification: CodingVerificationResult,
    codex_runtime: CodexRuntime,
    codex_execution_policy: CodexExecutionPolicy | None = None,
    handoff_policy: HandoffPolicy | None = None,
    verification_plan: VerificationPlan | None = None,
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
    escalation = decide_escalation(handoff, verification_passed=verification.passed)
    if verification.passed:
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
        outcome=_outcome(codex_execution),
    )


def _outcome(result: CodexEscalationExecutionResult) -> PostFailurePipelineOutcome:
    if result.failure_kind == CodexEscalationExecutionFailureKind.EXECUTION_DISABLED:
        return PostFailurePipelineOutcome.CODEX_EXECUTION_DISABLED
    if result.succeeded:
        return PostFailurePipelineOutcome.CODEX_EXECUTION_SUCCEEDED
    return PostFailurePipelineOutcome.CODEX_EXECUTION_FAILED
