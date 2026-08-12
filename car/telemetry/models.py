"""Privacy-preserving telemetry contracts; they contain no task or source content."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from car.codex_write.models import CodexSourceState
from car.router.models import Route


class _TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttemptCapability(StrEnum):
    L0 = "l0"
    GEMINI = "gemini"
    CODEX_READ_ONLY = "codex_read_only"
    CODEX_CONTROLLED_WRITE = "codex_controlled_write"


class UsageSource(StrEnum):
    PROVIDER_REPORTED = "provider_reported"
    RUNTIME_REPORTED = "runtime_reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class FinalOutcome(StrEnum):
    VERIFIED_SUCCESS = "verified_success"
    FAILED = "failed"
    RESTORED = "restored"
    UNCHANGED = "unchanged"
    UNCERTAIN = "uncertain"
    DIAGNOSTIC_ONLY = "diagnostic_only"


class VerificationTelemetry(_TelemetryModel):
    attempted: bool
    passed: bool | None = None
    check_count: int = Field(default=0, ge=0)
    passed_check_count: int = Field(default=0, ge=0)
    failed_check_count: int = Field(default=0, ge=0)
    timeout_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "VerificationTelemetry":
        if self.passed_check_count + self.failed_check_count > self.check_count:
            raise ValueError("verification counts exceed check count")
        if self.passed is True and self.failed_check_count:
            raise ValueError("passed verification cannot contain failed checks")
        return self


class TokenUsage(_TelemetryModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_input_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens_included_in_output: bool = False
    source: UsageSource = UsageSource.UNAVAILABLE

    @model_validator(mode="after")
    def unavailable_usage_is_unknown(self) -> "TokenUsage":
        values = (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cached_input_tokens,
            self.cache_write_input_tokens,
            self.total_tokens,
        )
        if self.source == UsageSource.UNAVAILABLE and any(value is not None for value in values):
            raise ValueError("unavailable token usage must remain unknown")
        return self


class ExecutionAttemptTelemetry(_TelemetryModel):
    sequence: int = Field(ge=1)
    capability: AttemptCapability
    provider: str | None = None
    model: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    attempted: bool
    succeeded: bool | None = None
    usage: TokenUsage | None = None
    verification: VerificationTelemetry | None = None
    failure_kind: str | None = None

    @model_validator(mode="after")
    def completion_is_consistent(self) -> "ExecutionAttemptTelemetry":
        if self.finished_at is None and self.duration_ms is not None:
            raise ValueError("unfinished attempt cannot have a duration")
        if not self.attempted and (self.succeeded is not None or self.failure_kind is not None):
            raise ValueError("unattempted capability cannot have an outcome")
        return self


class ExecutionTelemetry(_TelemetryModel):
    execution_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    task_category: str | None = None
    initial_route: Route | None = None
    final_route: Route | None = None
    attempts: tuple[ExecutionAttemptTelemetry, ...] = ()
    verification: VerificationTelemetry | None = None
    escalated: bool = False
    escalation_from: AttemptCapability | None = None
    escalation_to: AttemptCapability | None = None
    escalation_reason: str | None = None
    final_outcome: FinalOutcome | None = None
    source_state: CodexSourceState | None = None
    verified_success: bool | None = None

    @model_validator(mode="after")
    def verified_success_requires_authoritative_outcome(self) -> "ExecutionTelemetry":
        if self.verified_success is True and self.final_outcome != FinalOutcome.VERIFIED_SUCCESS:
            raise ValueError("verified success requires the verified-success outcome")
        if self.source_state == CodexSourceState.UNCERTAIN and self.verified_success is True:
            raise ValueError("uncertain source state cannot be a verified success")
        if self.finished_at is None and self.duration_ms is not None:
            raise ValueError("unfinished execution cannot have a duration")
        return self
