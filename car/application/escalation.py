"""Read-only coordinator for an already-authorized Codex escalation."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from car.application.codex import (
    CodexApplicationResult,
    CodexExecutionPolicy,
    execute_codex_handoff,
)
from car.codex.runtime import CodexRuntime
from car.escalation.models import CodexHandoff, EscalationDecision, EscalationReason
from car.router.models import Route


class CodexEscalationExecutionFailureKind(StrEnum):
    ESCALATION_NOT_AUTHORIZED = "escalation_not_authorized"
    INVALID_ESCALATION_TARGET = "invalid_escalation_target"
    MISSING_HANDOFF = "missing_handoff"
    WORKSPACE_STATE_UNCERTAIN = "workspace_state_uncertain"
    EXECUTION_DISABLED = "execution_disabled"
    CODEX_APPLICATION_FAILED = "codex_application_failed"


class CodexEscalationExecutionResult(BaseModel):
    escalation_authorized: bool
    attempted: bool
    succeeded: bool
    target: Route | None = None
    application_result: CodexApplicationResult | None = None
    failure_kind: CodexEscalationExecutionFailureKind | None = None


def execute_codex_escalation(
    repository_root: Path,
    decision: EscalationDecision,
    handoff: CodexHandoff | None,
    runtime: CodexRuntime,
    execution_policy: CodexExecutionPolicy | None = None,
) -> CodexEscalationExecutionResult:
    """Delegate one safe, authorized escalation to the existing application service."""
    if not decision.should_escalate:
        return _blocked(
            decision,
            CodexEscalationExecutionFailureKind.ESCALATION_NOT_AUTHORIZED,
        )
    if decision.target != Route.CODEX:
        return _blocked(decision, CodexEscalationExecutionFailureKind.INVALID_ESCALATION_TARGET)
    if handoff is None:
        return _blocked(decision, CodexEscalationExecutionFailureKind.MISSING_HANDOFF)
    if _workspace_uncertain(handoff):
        return _blocked(decision, CodexEscalationExecutionFailureKind.WORKSPACE_STATE_UNCERTAIN)
    active = execution_policy or CodexExecutionPolicy()
    if not active.enabled:
        return _blocked(
            decision,
            CodexEscalationExecutionFailureKind.EXECUTION_DISABLED,
            authorized=True,
        )
    application_result = execute_codex_handoff(repository_root, handoff, runtime, active)
    return CodexEscalationExecutionResult(
        escalation_authorized=True,
        attempted=application_result.attempted,
        succeeded=application_result.succeeded,
        target=decision.target,
        application_result=application_result,
        failure_kind=(
            None
            if application_result.succeeded
            else CodexEscalationExecutionFailureKind.CODEX_APPLICATION_FAILED
        ),
    )


def _blocked(
    decision: EscalationDecision,
    failure_kind: CodexEscalationExecutionFailureKind,
    *,
    authorized: bool = False,
) -> CodexEscalationExecutionResult:
    return CodexEscalationExecutionResult(
        escalation_authorized=authorized,
        attempted=False,
        succeeded=False,
        target=decision.target,
        failure_kind=failure_kind,
    )


def _workspace_uncertain(handoff: CodexHandoff) -> bool:
    return (
        handoff.escalation_reason == EscalationReason.WORKSPACE_STATE_UNCERTAIN
        or handoff.verification.rollback_failure is not None
    )
