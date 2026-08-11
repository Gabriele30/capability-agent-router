"""A small monotonic-clock collector for in-memory execution telemetry."""

from datetime import UTC, datetime
from time import monotonic
from uuid import uuid4

from car.codex_write.models import CodexSourceState
from car.router.models import Route
from car.telemetry.models import (
    AttemptCapability,
    ExecutionAttemptTelemetry,
    ExecutionTelemetry,
    FinalOutcome,
    TokenUsage,
    VerificationTelemetry,
)


class ExecutionTelemetryCollector:
    """Collect structured observations without affecting the observed execution."""

    def __init__(self, *, wall_clock=None, monotonic_clock=None) -> None:
        self._wall_clock = wall_clock or (lambda: datetime.now(UTC))
        self._monotonic_clock = monotonic_clock or monotonic
        self._started_at: datetime | None = None
        self._started_tick: float | None = None
        self._execution_id: str | None = None
        self._initial_route: Route | None = None
        self._task_category: str | None = None
        self._attempts: list[ExecutionAttemptTelemetry] = []
        self._attempt_ticks: dict[int, float] = {}
        self._verification: VerificationTelemetry | None = None
        self._escalation: tuple[AttemptCapability, AttemptCapability, str | None] | None = None

    def start_execution(self, *, initial_route: Route, task_category: str | None = None) -> str:
        if self._execution_id is not None:
            raise RuntimeError("execution telemetry already started")
        self._execution_id = str(uuid4())
        self._started_at = self._wall_clock()
        self._started_tick = self._monotonic_clock()
        self._initial_route = initial_route
        self._task_category = task_category
        return self._execution_id

    def start_attempt(
        self,
        capability: AttemptCapability,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> int:
        self._require_started()
        sequence = len(self._attempts) + 1
        self._attempts.append(
            ExecutionAttemptTelemetry(
                sequence=sequence,
                capability=capability,
                provider=provider,
                model=model,
                started_at=self._wall_clock(),
                attempted=True,
            )
        )
        self._attempt_ticks[sequence] = self._monotonic_clock()
        return sequence

    def finish_attempt(
        self,
        sequence: int,
        *,
        succeeded: bool,
        failure_kind: str | None = None,
        usage: TokenUsage | None = None,
        verification: VerificationTelemetry | None = None,
    ) -> None:
        attempt = self._attempts[sequence - 1]
        elapsed = self._duration(self._attempt_ticks.pop(sequence))
        self._attempts[sequence - 1] = attempt.model_copy(
            update={
                "finished_at": self._wall_clock(),
                "duration_ms": elapsed,
                "succeeded": succeeded,
                "failure_kind": failure_kind,
                "usage": usage,
                "verification": verification,
            }
        )

    def record_verification(self, verification: VerificationTelemetry) -> None:
        self._verification = verification

    def record_escalation(
        self, source: AttemptCapability, target: AttemptCapability, *, reason: str | None = None
    ) -> None:
        self._escalation = (source, target, reason)

    def finish_execution(
        self,
        *,
        final_route: Route,
        final_outcome: FinalOutcome,
        verified_success: bool | None,
        source_state: CodexSourceState | None = None,
    ) -> ExecutionTelemetry:
        self._require_started()
        escalation = self._escalation
        return ExecutionTelemetry(
            execution_id=self._execution_id or "",
            started_at=self._started_at or self._wall_clock(),
            finished_at=self._wall_clock(),
            duration_ms=self._duration(self._started_tick),
            task_category=self._task_category,
            initial_route=self._initial_route,
            final_route=final_route,
            attempts=tuple(self._attempts),
            verification=self._verification,
            escalated=escalation is not None,
            escalation_from=escalation[0] if escalation else None,
            escalation_to=escalation[1] if escalation else None,
            escalation_reason=escalation[2] if escalation else None,
            final_outcome=final_outcome,
            source_state=source_state,
            verified_success=verified_success,
        )

    def _require_started(self) -> None:
        if self._execution_id is None:
            raise RuntimeError("execution telemetry has not started")

    def _duration(self, started_tick: float | None) -> int:
        if started_tick is None:
            raise RuntimeError("monotonic start tick is missing")
        return round((self._monotonic_clock() - started_tick) * 1000)
