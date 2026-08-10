"""Application composition for CLI and future integrations."""

from car.application.codex import (
    CodexApplicationFailureKind,
    CodexApplicationResult,
    CodexExecutionPolicy,
    execute_codex_handoff,
)
from car.application.escalation import (
    CodexEscalationExecutionFailureKind,
    CodexEscalationExecutionResult,
    execute_codex_escalation,
)
from car.application.post_failure import (
    PostFailurePipelineOutcome,
    PostFailurePipelineResult,
    process_verified_coding_outcome,
)

__all__ = [
    "CodexApplicationFailureKind",
    "CodexApplicationResult",
    "CodexExecutionPolicy",
    "CodexEscalationExecutionFailureKind",
    "CodexEscalationExecutionResult",
    "execute_codex_escalation",
    "execute_codex_handoff",
    "PostFailurePipelineOutcome",
    "PostFailurePipelineResult",
    "process_verified_coding_outcome",
]
