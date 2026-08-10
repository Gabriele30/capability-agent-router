"""Application composition for CLI and future integrations."""

from car.application.codex import (
    CodexApplicationFailureKind,
    CodexApplicationResult,
    CodexExecutionPolicy,
    execute_codex_handoff,
)
from car.application.coding import (
    CodingPipelineOutcome,
    CodingPipelineResult,
    execute_coding_pipeline,
)
from car.application.coding_execution import (
    CodingPipelineApplicationFailureKind,
    CodingPipelineApplicationResult,
    CodingPipelineExecutionPolicy,
    execute_authorized_coding_pipeline,
)
from car.application.coding_flow import CodingFlowOutcome, CodingFlowResult, execute_coding_flow
from car.application.escalation import (
    CodexEscalationExecutionFailureKind,
    CodexEscalationExecutionResult,
    execute_codex_escalation,
)
from car.application.execution_gateway import (
    CodingFlowAuthorization,
    CodingFlowExecutionRequest,
    CodingFlowGateway,
    CodingFlowGatewayFailureKind,
    CodingFlowGatewayResult,
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
    "CodingPipelineOutcome",
    "CodingPipelineResult",
    "execute_coding_pipeline",
    "CodingPipelineApplicationFailureKind",
    "CodingPipelineApplicationResult",
    "CodingPipelineExecutionPolicy",
    "execute_authorized_coding_pipeline",
    "CodingFlowOutcome",
    "CodingFlowResult",
    "execute_coding_flow",
    "CodingFlowAuthorization",
    "CodingFlowExecutionRequest",
    "CodingFlowGateway",
    "CodingFlowGatewayFailureKind",
    "CodingFlowGatewayResult",
]
