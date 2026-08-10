"""Bounded, serializable evidence for a future Codex handoff."""

from enum import StrEnum

from pydantic import BaseModel, Field

from car.router.models import Route


class EscalationReason(StrEnum):
    NO_ESCALATION_SUCCESS = "no_escalation_success"
    ROUTE_DOES_NOT_ALLOW_ESCALATION = "route_does_not_allow_escalation"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_TIMEOUT = "verification_timeout"
    WORKSPACE_STATE_UNCERTAIN = "workspace_state_uncertain"
    CODING_ATTEMPT_FAILED = "coding_attempt_failed"
    PATCH_VALIDATION_FAILED = "patch_validation_failed"
    PATCH_APPLY_FAILED = "patch_apply_failed"


class HandoffPolicy(BaseModel):
    max_selected_files: int = Field(default=20, ge=1)
    max_patch_chars: int = Field(default=8000, ge=100)
    max_check_output_chars: int = Field(default=4000, ge=100)
    max_reasons: int = Field(default=10, ge=1)


class RoutingHandoffSummary(BaseModel):
    deterministic_route: Route
    final_route: Route
    decision_sources: list[str]
    fusion_reasons: list[str]
    provider_influenced_decision: bool
    deterministic_risk: float
    provider_risk: float | None = None
    final_risk: float


class RepositoryHandoffSummary(BaseModel):
    name: str
    branch: str | None
    dirty: bool
    languages: dict[str, int]
    systems: list[str]


class CodingAttemptSummary(BaseModel):
    provider: str
    attempted: bool
    succeeded: bool
    proposal_summary: str | None = None
    reasons: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    error_kind: str | None = None


class PatchAttemptSummary(BaseModel):
    paths: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)
    diffs: list[str] = Field(default_factory=list)
    validation_valid: bool | None = None
    validation_violations: list[str] = Field(default_factory=list)
    apply_succeeded: bool | None = None
    apply_failure: str | None = None


class VerificationHandoffSummary(BaseModel):
    planned_checks: list[list[str]] = Field(default_factory=list)
    executed_checks: list[dict[str, object]] = Field(default_factory=list)
    failure_kind: str | None = None
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None
    rollback_failure: str | None = None


class CodexHandoff(BaseModel):
    task: str
    routing: RoutingHandoffSummary
    repository: RepositoryHandoffSummary
    selected_files: list[str]
    coding_attempt: CodingAttemptSummary
    patch_attempt: PatchAttemptSummary
    verification: VerificationHandoffSummary
    escalation_reason: EscalationReason
    recommended_next_step: str


class EscalationDecision(BaseModel):
    should_escalate: bool
    target: Route | None = None
    reason: EscalationReason
