"""Compose one provider-neutral coding attempt through CAR's safe patch pipeline."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from car.coding.base import CodingProvider
from car.coding.models import CodingAttemptResult, CodingExecutionPolicy, CodingTaskContext
from car.coding.orchestration import attempt_coding
from car.coding.verification import (
    CodingVerificationCoordinator,
    CodingVerificationFailureKind,
    CodingVerificationResult,
)
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchApplyResult, PatchValidationPolicy, PatchValidationResult
from car.patching.validation import PatchValidator
from car.router.consultation import RoutingEvaluation
from car.router.models import Route
from car.verification.models import VerificationPlan


class CodingPipelineOutcome(StrEnum):
    ROUTE_NOT_ELIGIBLE = "route_not_eligible"
    CODING_PROVIDER_UNAVAILABLE = "coding_provider_unavailable"
    CODING_PROVIDER_FAILED = "coding_provider_failed"
    PATCH_VALIDATION_FAILED = "patch_validation_failed"
    PATCH_APPLY_FAILED = "patch_apply_failed"
    VERIFICATION_FAILED = "verification_failed"
    SUCCEEDED = "succeeded"


class CodingPipelineResult(BaseModel):
    attempted: bool
    succeeded: bool
    route: Route
    coding_attempt: CodingAttemptResult | None = None
    patch_validation: PatchValidationResult | None = None
    patch_apply: PatchApplyResult | None = None
    verification: CodingVerificationResult | None = None
    outcome: CodingPipelineOutcome


def execute_coding_pipeline(
    *,
    repository_root: Path,
    routing_evaluation: RoutingEvaluation,
    coding_context: CodingTaskContext,
    coding_provider: CodingProvider,
    coding_policy: CodingExecutionPolicy | None,
    patch_validation_policy: PatchValidationPolicy | None,
    verification_plan: VerificationPlan,
    patch_applier: SafePatchApplier | None = None,
    verification_coordinator: CodingVerificationCoordinator | None = None,
) -> CodingPipelineResult:
    """Perform at most one proposal, validation, apply, and verification cycle."""
    route = routing_evaluation.final_decision.route
    if route not in {Route.GEMINI, Route.GEMINI_TO_CODEX}:
        return CodingPipelineResult(
            attempted=False,
            succeeded=False,
            route=route,
            outcome=CodingPipelineOutcome.ROUTE_NOT_ELIGIBLE,
        )
    policy = coding_policy or CodingExecutionPolicy()
    validation_policy = patch_validation_policy or PatchValidationPolicy()
    prompt_context = coding_context.model_copy(
        update={"safe_auxiliary_paths": validation_policy.safe_auxiliary_paths}
    )
    attempt = attempt_coding(prompt_context, coding_provider, policy)
    if not attempt.succeeded:
        return CodingPipelineResult(
            attempted=attempt.attempted,
            succeeded=False,
            route=route,
            coding_attempt=attempt,
            outcome=(
                CodingPipelineOutcome.CODING_PROVIDER_UNAVAILABLE
                if not attempt.attempted
                else CodingPipelineOutcome.CODING_PROVIDER_FAILED
            ),
        )
    validation = PatchValidator(validation_policy).validate(
        attempt.proposal, prompt_context, repository_root, policy
    )
    if not validation.valid or validation.patch_set is None:
        return CodingPipelineResult(
            attempted=True,
            succeeded=False,
            route=route,
            coding_attempt=attempt,
            patch_validation=validation,
            outcome=CodingPipelineOutcome.PATCH_VALIDATION_FAILED,
        )
    transaction = (patch_applier or SafePatchApplier()).apply(repository_root, validation.patch_set)
    apply = transaction.result
    if not apply.succeeded:
        return CodingPipelineResult(
            attempted=True,
            succeeded=False,
            route=route,
            coding_attempt=attempt,
            patch_validation=validation,
            patch_apply=apply,
            outcome=CodingPipelineOutcome.PATCH_APPLY_FAILED,
        )
    try:
        verification = (verification_coordinator or CodingVerificationCoordinator()).verify(
            repository_root, transaction, verification_plan
        )
    except Exception:
        rolled_back = transaction.rollback()
        verification = CodingVerificationResult(
            attempted=False,
            passed=False,
            rolled_back=rolled_back,
            failure_kind=CodingVerificationFailureKind.VERIFICATION_INTERNAL_ERROR,
            rollback_failure=(
                None if rolled_back else CodingVerificationFailureKind.ROLLBACK_FAILED
            ),
            message="verification coordinator failed",
        )
    return CodingPipelineResult(
        attempted=True,
        succeeded=verification.passed,
        route=route,
        coding_attempt=attempt,
        patch_validation=validation,
        patch_apply=apply,
        verification=verification,
        outcome=(
            CodingPipelineOutcome.SUCCEEDED
            if verification.passed
            else CodingPipelineOutcome.VERIFICATION_FAILED
        ),
    )
