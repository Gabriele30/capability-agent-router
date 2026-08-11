from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from car.codex_write.models import CodexSourceState
from car.router.models import Route
from car.telemetry import (
    AttemptCapability,
    ExecutionTelemetry,
    ExecutionTelemetryCollector,
    FinalOutcome,
    TokenUsage,
    UsageSource,
    VerificationTelemetry,
)


class _Clock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 8, 11, tzinfo=UTC)
        self.tick = 10.0

    def wall_clock(self) -> datetime:
        return self.wall

    def monotonic_clock(self) -> float:
        return self.tick

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.tick += seconds


def test_collector_records_ordered_escalation_and_monotonic_duration():
    clock = _Clock()
    collector = ExecutionTelemetryCollector(
        wall_clock=clock.wall_clock, monotonic_clock=clock.monotonic_clock
    )
    collector.start_execution(initial_route=Route.GEMINI_TO_CODEX, task_category="bugfix")
    gemini = collector.start_attempt(AttemptCapability.GEMINI, provider="gemini", model="test")
    clock.advance(1.2)
    collector.finish_attempt(gemini, succeeded=False, failure_kind="check_timeout")
    collector.record_escalation(
        AttemptCapability.GEMINI,
        AttemptCapability.CODEX_CONTROLLED_WRITE,
        reason="verification_failed",
    )
    codex = collector.start_attempt(AttemptCapability.CODEX_CONTROLLED_WRITE, provider="codex")
    clock.advance(0.8)
    collector.finish_attempt(codex, succeeded=True)
    verification = VerificationTelemetry(
        attempted=True, passed=True, check_count=1, passed_check_count=1
    )
    collector.record_verification(verification)
    clock.advance(0.5)
    telemetry = collector.finish_execution(
        final_route=Route.GEMINI_TO_CODEX,
        final_outcome=FinalOutcome.VERIFIED_SUCCESS,
        verified_success=True,
        source_state=CodexSourceState.UPDATED_AND_ACCEPTED,
    )

    assert [attempt.capability for attempt in telemetry.attempts] == [
        AttemptCapability.GEMINI,
        AttemptCapability.CODEX_CONTROLLED_WRITE,
    ]
    assert telemetry.attempts[0].duration_ms == 1200
    assert telemetry.duration_ms == 2500
    assert telemetry.escalated and telemetry.verified_success
    assert telemetry.model_dump(mode="json")["attempts"][0]["usage"] is None


def test_unknown_usage_is_explicit_and_never_zero():
    usage = TokenUsage()
    assert usage.source == UsageSource.UNAVAILABLE
    assert usage.input_tokens is None and usage.total_tokens is None
    with pytest.raises(ValidationError):
        TokenUsage(source=UsageSource.UNAVAILABLE, total_tokens=0)


def test_privacy_and_success_invariants_are_enforced():
    with pytest.raises(ValidationError):
        ExecutionTelemetry(
            execution_id="opaque",
            started_at=datetime.now(UTC),
            final_outcome=FinalOutcome.FAILED,
            verified_success=True,
        )
    with pytest.raises(ValidationError):
        ExecutionTelemetry(
            execution_id="opaque",
            started_at=datetime.now(UTC),
            final_outcome=FinalOutcome.VERIFIED_SUCCESS,
            verified_success=True,
            source_state=CodexSourceState.UNCERTAIN,
        )
    schema = ExecutionTelemetry.model_json_schema()
    serialized = str(schema)
    for forbidden in ("prompt", "content", "environment", "repository_root", "stdout", "stderr"):
        assert forbidden not in serialized
