"""Explicit user-authorization boundary for the internal coding flow."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from car.application.codex import CodexExecutionPolicy
from car.application.coding_execution import CodingPipelineExecutionPolicy
from car.application.coding_flow import CodingFlowResult, execute_coding_flow
from car.codex.runtime import CodexRuntime
from car.coding.base import CodingProvider
from car.coding.models import CodingExecutionPolicy, CodingTaskContext
from car.coding.verification import CodingVerificationCoordinator
from car.escalation.models import HandoffPolicy
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchValidationPolicy
from car.repository.models import RepositoryState
from car.router.consultation import RoutingEvaluation
from car.verification.models import VerificationPlan


class CodingFlowAuthorization(BaseModel):
    """Caller-owned runtime authorization; it cannot alter CAR safety policies."""

    authorized: bool = False


class CodingFlowGatewayFailureKind(StrEnum):
    NOT_AUTHORIZED = "not_authorized"
    FLOW_FAILED = "flow_failed"
    INVALID_REQUEST = "invalid_request"


class CodingFlowExecutionRequest(BaseModel):
    """All safety-relevant inputs are explicit and already prepared by the caller."""

    repository_root: Path
    routing_evaluation: RoutingEvaluation
    repository_state: RepositoryState
    coding_context: CodingTaskContext
    coding_policy: CodingExecutionPolicy | None
    patch_validation_policy: PatchValidationPolicy | None
    verification_plan: VerificationPlan
    coding_execution_policy: CodingPipelineExecutionPolicy | None
    handoff_policy: HandoffPolicy | None
    codex_execution_policy: CodexExecutionPolicy | None


class CodingFlowGatewayResult(BaseModel):
    authorized: bool
    attempted: bool
    succeeded: bool
    flow_result: CodingFlowResult | None = None
    failure_kind: CodingFlowGatewayFailureKind | None = None


class CodingFlowGateway:
    """Delegate exactly one explicitly authorized flow using caller-injected dependencies."""

    def __init__(
        self,
        coding_provider: CodingProvider,
        codex_runtime: CodexRuntime,
        *,
        patch_applier: SafePatchApplier | None = None,
        verification_coordinator: CodingVerificationCoordinator | None = None,
    ) -> None:
        self._coding_provider = coding_provider
        self._codex_runtime = codex_runtime
        self._patch_applier = patch_applier
        self._verification_coordinator = verification_coordinator

    def execute(
        self,
        request: CodingFlowExecutionRequest,
        authorization: CodingFlowAuthorization | None = None,
    ) -> CodingFlowGatewayResult:
        active_authorization = authorization or CodingFlowAuthorization()
        if not active_authorization.authorized:
            return CodingFlowGatewayResult(
                authorized=False,
                attempted=False,
                succeeded=False,
                failure_kind=CodingFlowGatewayFailureKind.NOT_AUTHORIZED,
            )
        flow_result = execute_coding_flow(
            repository_root=request.repository_root,
            routing_evaluation=request.routing_evaluation,
            repository_state=request.repository_state,
            coding_context=request.coding_context,
            coding_provider=self._coding_provider,
            coding_policy=request.coding_policy,
            patch_validation_policy=request.patch_validation_policy,
            verification_plan=request.verification_plan,
            coding_execution_policy=request.coding_execution_policy,
            handoff_policy=request.handoff_policy,
            codex_runtime=self._codex_runtime,
            codex_execution_policy=request.codex_execution_policy,
            patch_applier=self._patch_applier,
            verification_coordinator=self._verification_coordinator,
        )
        return CodingFlowGatewayResult(
            authorized=True,
            attempted=flow_result.attempted,
            succeeded=flow_result.succeeded,
            flow_result=flow_result,
            failure_kind=None
            if flow_result.succeeded
            else CodingFlowGatewayFailureKind.FLOW_FAILED,
        )
