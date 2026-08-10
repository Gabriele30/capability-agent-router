"""Offline safety tests for explicit application-layer Codex execution."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

from car.application.codex import (
    CodexApplicationFailureKind,
    CodexExecutionPolicy,
    execute_codex_handoff,
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
        self.health_call_count = 0
        self.execute_call_count = 0
        self.last_request = None

    def health(self) -> CodexRuntimeHealth:
        self.health_call_count += 1
        return self.health_result

    def execute(self, request) -> CodexExecutionResult:
        self.execute_call_count += 1
        self.last_request = request
        return self.execution_result


def _handoff() -> CodexHandoff:
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
            failure_kind="check_failed", rollback_attempted=True, rollback_succeeded=True
        ),
        escalation_reason=EscalationReason.VERIFICATION_FAILED,
        recommended_next_step="Inspect the failed verification evidence.",
    )


def _runtime(
    status: CodexRuntimeHealthStatus = CodexRuntimeHealthStatus.READY,
    execution: CodexExecutionResult | None = None,
) -> FakeCodexRuntime:
    return FakeCodexRuntime(
        CodexRuntimeHealth(status=status),
        execution or CodexExecutionResult(attempted=True, succeeded=True, final_message="plan"),
    )


def test_disabled_policy_does_not_touch_runtime(tmp_path: Path):
    runtime = _runtime()

    result = execute_codex_handoff(tmp_path, _handoff(), runtime)

    assert not result.attempted and not result.succeeded
    assert result.failure_kind == CodexApplicationFailureKind.DISABLED
    assert runtime.health_call_count == runtime.execute_call_count == 0


def test_not_ready_health_blocks_execution(tmp_path: Path):
    for status in (
        CodexRuntimeHealthStatus.CLI_NOT_FOUND,
        CodexRuntimeHealthStatus.NOT_AUTHENTICATED,
        CodexRuntimeHealthStatus.UNKNOWN,
    ):
        runtime = _runtime(status)
        result = execute_codex_handoff(
            tmp_path, _handoff(), runtime, CodexExecutionPolicy(enabled=True)
        )
        assert not result.attempted and not result.succeeded
        assert result.health_status == status
        assert result.failure_kind == CodexApplicationFailureKind.RUNTIME_NOT_READY
        assert runtime.health_call_count == 1 and runtime.execute_call_count == 0


def test_ready_execution_forwards_handoff_and_policy_timeout(tmp_path: Path):
    handoff = _handoff()
    runtime = _runtime()

    result = execute_codex_handoff(
        tmp_path,
        handoff,
        runtime,
        CodexExecutionPolicy(enabled=True, timeout_seconds=37),
    )

    assert result.attempted and result.succeeded and result.failure_kind is None
    assert runtime.health_call_count == runtime.execute_call_count == 1
    assert runtime.last_request.handoff is handoff
    assert runtime.last_request.timeout_seconds == 37
    assert runtime.last_request.handoff.coding_attempt.proposal_summary == "Fix parser"
    assert runtime.last_request.handoff.patch_attempt.diffs == ["@@ -1 +1 @@\n-old\n+new"]
    assert runtime.last_request.handoff.verification.rollback_succeeded is True


def test_ready_runtime_failure_is_preserved_without_retry(tmp_path: Path):
    execution = CodexExecutionResult(
        attempted=True,
        succeeded=False,
        failure_kind=CodexRuntimeFailureKind.TIMEOUT,
        timed_out=True,
    )
    runtime = _runtime(execution=execution)

    result = execute_codex_handoff(
        tmp_path, _handoff(), runtime, CodexExecutionPolicy(enabled=True)
    )

    assert result.attempted and not result.succeeded
    assert result.failure_kind == CodexApplicationFailureKind.EXECUTION_FAILED
    assert result.execution is execution
    assert result.execution.failure_kind == CodexRuntimeFailureKind.TIMEOUT
    assert runtime.execute_call_count == 1


def test_ready_nonzero_result_is_preserved(tmp_path: Path):
    execution = CodexExecutionResult(
        attempted=True,
        succeeded=False,
        exit_code=1,
        failure_kind=CodexRuntimeFailureKind.NONZERO_EXIT,
    )
    runtime = _runtime(execution=execution)

    result = execute_codex_handoff(
        tmp_path, _handoff(), runtime, CodexExecutionPolicy(enabled=True)
    )

    assert result.failure_kind == CodexApplicationFailureKind.EXECUTION_FAILED
    assert result.execution is execution
    assert runtime.execute_call_count == 1


def test_application_service_is_read_only_without_subprocess_or_persistence(
    tmp_path: Path, monkeypatch
):
    tracked = tmp_path / "tracked.py"
    tracked.write_bytes(b"original")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}

    def fail(*args, **kwargs):
        raise AssertionError("application service must not execute subprocesses")

    monkeypatch.setattr(subprocess, "run", fail)
    result = execute_codex_handoff(
        tmp_path, _handoff(), _runtime(), CodexExecutionPolicy(enabled=True)
    )

    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*")}
    assert result.succeeded
    assert after == before
    assert not (tmp_path / ".car-context").exists()


def test_application_source_has_no_runtime_credentials_or_markdown_parser():
    source = inspect.getsource(execute_codex_handoff)
    for forbidden in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "parse_codex_handoff_markdown",
        "write_codex_handoff",
        "subprocess",
    ):
        assert forbidden not in source
