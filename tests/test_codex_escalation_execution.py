"""Offline tests for the read-only authorized Codex escalation coordinator."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

from car.application.codex import CodexExecutionPolicy
from car.application.escalation import (
    CodexEscalationExecutionFailureKind,
    execute_codex_escalation,
)
from car.codex.models import (
    CodexExecutionResult,
    CodexRuntimeFailureKind,
    CodexRuntimeHealth,
    CodexRuntimeHealthStatus,
)
from car.escalation.models import (
    CodexHandoff,
    CodingAttemptSummary,
    EscalationDecision,
    EscalationReason,
    PatchAttemptSummary,
    RepositoryHandoffSummary,
    RoutingHandoffSummary,
    VerificationHandoffSummary,
)
from car.router.models import Route


class FakeCodexRuntime:
    def __init__(self, health: CodexRuntimeHealth, execution: CodexExecutionResult) -> None:
        self.health_result = health
        self.execution_result = execution
        self.health_calls = 0
        self.execute_calls = 0
        self.last_request = None

    def health(self):
        self.health_calls += 1
        return self.health_result

    def execute(self, request):
        self.execute_calls += 1
        self.last_request = request
        return self.execution_result


def _handoff(*, uncertain: bool = False) -> CodexHandoff:
    return CodexHandoff(
        task="Fix parser regression",
        routing=RoutingHandoffSummary(
            deterministic_route=Route.GEMINI_TO_CODEX,
            final_route=Route.GEMINI_TO_CODEX,
            decision_sources=["deterministic"],
            fusion_reasons=["verification_failed"],
            provider_influenced_decision=False,
            deterministic_risk=0.4,
            final_risk=0.4,
        ),
        repository=RepositoryHandoffSummary(
            name="repo", branch="main", dirty=True, languages={"Python": 1}, systems=["Python"]
        ),
        selected_files=["car/parser.py"],
        coding_attempt=CodingAttemptSummary(
            provider="gemini", attempted=True, succeeded=True, proposal_summary="Fix parser"
        ),
        patch_attempt=PatchAttemptSummary(
            paths=["car/parser.py"],
            operations=["modify"],
            diffs=["@@ -1 +1 @@\n-old\n+new"],
            validation_valid=True,
            apply_succeeded=True,
        ),
        verification=VerificationHandoffSummary(
            failure_kind="check_failed",
            rollback_attempted=True,
            rollback_succeeded=not uncertain,
            rollback_failure="rollback_failed" if uncertain else None,
        ),
        escalation_reason=(
            EscalationReason.WORKSPACE_STATE_UNCERTAIN
            if uncertain
            else EscalationReason.VERIFICATION_FAILED
        ),
        recommended_next_step="Inspect failure evidence.",
    )


def _decision(*, allowed: bool = True, target: Route | None = Route.CODEX) -> EscalationDecision:
    return EscalationDecision(
        should_escalate=allowed,
        target=target,
        reason=EscalationReason.VERIFICATION_FAILED,
    )


def _runtime(
    status: CodexRuntimeHealthStatus = CodexRuntimeHealthStatus.READY,
    execution: CodexExecutionResult | None = None,
) -> FakeCodexRuntime:
    return FakeCodexRuntime(
        CodexRuntimeHealth(status=status),
        execution or CodexExecutionResult(attempted=True, succeeded=True, final_message="plan"),
    )


def test_unauthorized_wrong_target_missing_handoff_and_uncertain_workspace_block(tmp_path: Path):
    cases = [
        (
            _decision(allowed=False),
            _handoff(),
            CodexEscalationExecutionFailureKind.ESCALATION_NOT_AUTHORIZED,
        ),
        (
            _decision(target=Route.GEMINI),
            _handoff(),
            CodexEscalationExecutionFailureKind.INVALID_ESCALATION_TARGET,
        ),
        (_decision(), None, CodexEscalationExecutionFailureKind.MISSING_HANDOFF),
        (
            _decision(),
            _handoff(uncertain=True),
            CodexEscalationExecutionFailureKind.WORKSPACE_STATE_UNCERTAIN,
        ),
    ]
    for decision, handoff, failure in cases:
        runtime = _runtime()
        result = execute_codex_escalation(
            tmp_path, decision, handoff, runtime, CodexExecutionPolicy(enabled=True)
        )
        assert result.failure_kind == failure
        assert not result.attempted and runtime.health_calls == runtime.execute_calls == 0


def test_authorized_disabled_does_not_touch_runtime(tmp_path: Path):
    runtime = _runtime()

    result = execute_codex_escalation(tmp_path, _decision(), _handoff(), runtime)

    assert result.escalation_authorized and not result.attempted
    assert result.failure_kind == CodexEscalationExecutionFailureKind.EXECUTION_DISABLED
    assert runtime.health_calls == runtime.execute_calls == 0


def test_authorized_success_forwards_complete_handoff_once(tmp_path: Path):
    handoff = _handoff()
    runtime = _runtime()

    result = execute_codex_escalation(
        tmp_path,
        _decision(),
        handoff,
        runtime,
        CodexExecutionPolicy(enabled=True, timeout_seconds=37),
    )

    assert result.escalation_authorized and result.attempted and result.succeeded
    assert runtime.health_calls == runtime.execute_calls == 1
    assert runtime.last_request.handoff is handoff
    assert runtime.last_request.timeout_seconds == 37
    assert runtime.last_request.handoff.coding_attempt.proposal_summary == "Fix parser"
    assert runtime.last_request.handoff.patch_attempt.diffs == ["@@ -1 +1 @@\n-old\n+new"]
    assert runtime.last_request.handoff.verification.rollback_succeeded is True


def test_authorized_not_ready_and_runtime_failures_are_nested(tmp_path: Path):
    for status in (
        CodexRuntimeHealthStatus.CLI_NOT_FOUND,
        CodexRuntimeHealthStatus.NOT_AUTHENTICATED,
        CodexRuntimeHealthStatus.UNKNOWN,
    ):
        runtime = _runtime(status)
        result = execute_codex_escalation(
            tmp_path, _decision(), _handoff(), runtime, CodexExecutionPolicy(enabled=True)
        )
        assert result.failure_kind == CodexEscalationExecutionFailureKind.CODEX_APPLICATION_FAILED
        assert result.application_result.health_status == status
        assert runtime.health_calls == 1 and runtime.execute_calls == 0

    for failure in (CodexRuntimeFailureKind.TIMEOUT, CodexRuntimeFailureKind.NONZERO_EXIT):
        runtime = _runtime(
            execution=CodexExecutionResult(attempted=True, succeeded=False, failure_kind=failure)
        )
        result = execute_codex_escalation(
            tmp_path, _decision(), _handoff(), runtime, CodexExecutionPolicy(enabled=True)
        )
        assert result.application_result.execution.failure_kind == failure
        assert runtime.execute_calls == 1


def test_coordinator_is_read_only_without_subprocess_or_persistence(tmp_path: Path, monkeypatch):
    tracked = tmp_path / "tracked.py"
    tracked.write_bytes(b"original")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}

    def fail(*args, **kwargs):
        raise AssertionError("coordinator must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail)
    result = execute_codex_escalation(
        tmp_path, _decision(), _handoff(), _runtime(), CodexExecutionPolicy(enabled=True)
    )

    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}
    assert result.succeeded and after == before
    assert not (tmp_path / ".car-context").exists()


def test_coordinator_source_has_no_markdown_auth_environment_or_subprocess_access():
    source = inspect.getsource(execute_codex_escalation)
    for forbidden in (
        "render_codex_handoff_markdown",
        "write_codex_handoff",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "subprocess",
    ):
        assert forbidden not in source
