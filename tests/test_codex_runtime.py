"""Offline tests for the read-only local Codex CLI runtime foundation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from car.codex.models import (
    CodexExecutionRequest,
    CodexProcessResult,
    CodexRuntimeFailureKind,
    CodexRuntimeHealthStatus,
    CodexRuntimePolicy,
)
from car.codex.runtime import (
    READ_ONLY_INSTRUCTION,
    LocalCodexRuntime,
    SubprocessCodexRunner,
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


class FakeCodexRunner:
    def __init__(self, results: list[CodexProcessResult]) -> None:
        self.results = results
        self.calls: list[dict[str, object]] = []

    def run(self, argv, *, cwd, stdin, environment, timeout_seconds):
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "stdin": stdin,
                "environment": environment,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.results.pop(0)


def _handoff() -> CodexHandoff:
    return CodexHandoff(
        task="Fix parser regression",
        routing=RoutingHandoffSummary(
            deterministic_route=Route.GEMINI_TO_CODEX,
            final_route=Route.GEMINI_TO_CODEX,
            decision_sources=["deterministic"],
            fusion_reasons=["no_provider_evidence"],
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
            executed_checks=[
                {
                    "command": ["ruff", "check", "car/parser.py"],
                    "exit_code": 1,
                    "timeout": False,
                    "stdout": "failed check output",
                    "stderr": "failed check diagnostics",
                }
            ],
            failure_kind="check_failed",
            rollback_attempted=True,
            rollback_succeeded=True,
        ),
        escalation_reason=EscalationReason.VERIFICATION_FAILED,
        recommended_next_step="Inspect the failed verification evidence.",
    )


def _runtime(results: list[CodexProcessResult], *, policy: CodexRuntimePolicy | None = None):
    runner = FakeCodexRunner(results)
    return LocalCodexRuntime(
        runner=runner, which=lambda name: "C:/tools/codex.exe", policy=policy
    ), runner


def _request(root: Path) -> CodexExecutionRequest:
    return CodexExecutionRequest(repository_root=root, handoff=_handoff(), timeout_seconds=30)


def _ready() -> CodexProcessResult:
    return CodexProcessResult(exit_code=0, stdout="logged in")


def test_health_reports_cli_missing_without_starting_process():
    runner = FakeCodexRunner([])
    runtime = LocalCodexRuntime(runner=runner, which=lambda name: None)

    health = runtime.health()

    assert health.status == CodexRuntimeHealthStatus.CLI_NOT_FOUND
    assert runner.calls == []


def test_health_reports_ready_after_local_login_status():
    runtime, runner = _runtime([_ready()])

    health = runtime.health()

    assert health.status == CodexRuntimeHealthStatus.READY
    assert runner.calls[0]["argv"] == ["codex", "login", "status"]


@pytest.mark.parametrize(
    ("status_result", "expected"),
    [
        (
            CodexProcessResult(exit_code=1, stderr="not logged in"),
            CodexRuntimeHealthStatus.NOT_AUTHENTICATED,
        ),
        (CodexProcessResult(timed_out=True), CodexRuntimeHealthStatus.UNKNOWN),
    ],
)
def test_health_fails_closed_when_login_status_is_not_ready(status_result, expected):
    runtime, _ = _runtime([status_result])

    assert runtime.health().status == expected


def test_execute_builds_read_only_argv_and_sends_handoff_via_stdin(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-openai")
    monkeypatch.setenv("CODEX_API_KEY", "super-secret-codex")
    runtime, runner = _runtime(
        [_ready(), CodexProcessResult(exit_code=0, stdout="Corrective plan")]
    )

    result = runtime.execute(_request(tmp_path))

    call = runner.calls[1]
    argv = call["argv"]
    stdin = call["stdin"]
    assert result.succeeded and result.final_message == "Corrective plan"
    assert argv[:7] == [
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
    ]
    assert argv[-1] == READ_ONLY_INSTRUCTION
    assert not any(
        flag in argv for flag in ("workspace-write", "danger-full-access", "--yolo", "--full-auto")
    )
    assert "--skip-git-repo-check" not in argv
    assert "# CAR Codex Handoff" in stdin
    for expected in (
        "Fix parser regression",
        "Fix parser",
        "car/parser.py",
        "check_failed",
        "Succeeded: True",
    ):
        assert expected in stdin
    assert "Fix parser regression" not in argv
    assert "OPENAI_API_KEY" not in call["environment"]
    assert "CODEX_API_KEY" not in call["environment"]
    assert "super-secret-openai" not in str(result)
    assert "super-secret-codex" not in str(result)


def test_execute_stops_before_codex_when_auth_is_unavailable(tmp_path: Path):
    runtime, runner = _runtime([CodexProcessResult(exit_code=1)])

    result = runtime.execute(_request(tmp_path))

    assert not result.attempted
    assert result.failure_kind == CodexRuntimeFailureKind.NOT_AUTHENTICATED
    assert len(runner.calls) == 1


def test_execute_maps_timeout_nonzero_and_empty_output(tmp_path: Path):
    cases = [
        (CodexProcessResult(timed_out=True), CodexRuntimeFailureKind.TIMEOUT),
        (CodexProcessResult(exit_code=1, stderr="failed"), CodexRuntimeFailureKind.NONZERO_EXIT),
        (CodexProcessResult(exit_code=0, stdout=""), CodexRuntimeFailureKind.INVALID_OUTPUT),
    ]
    for process_result, expected in cases:
        runtime, _ = _runtime([_ready(), process_result])
        result = runtime.execute(_request(tmp_path))
        assert result.attempted and not result.succeeded
        assert result.failure_kind == expected


def test_execute_bounds_stdout_and_stderr(tmp_path: Path):
    runtime, _ = _runtime(
        [
            _ready(),
            CodexProcessResult(
                exit_code=1, stdout="o" * 150 + "STDOUT_END", stderr="e" * 150 + "STDERR_END"
            ),
        ],
        policy=CodexRuntimePolicy(max_stdout_chars=100, max_stderr_chars=100),
    )

    result = runtime.execute(_request(tmp_path))

    assert result.stdout.endswith("[truncated by CAR]")
    assert result.stderr.endswith("[truncated by CAR]")
    assert "STDOUT_END" not in result.stdout
    assert "STDERR_END" not in result.stderr


def test_execute_is_read_only_and_does_not_persist_handoff(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    tracked = tmp_path / "tracked.py"
    tracked.write_bytes(b"original")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    runtime, _ = _runtime([_ready(), CodexProcessResult(exit_code=0, stdout="Corrective plan")])

    runtime.execute(_request(tmp_path))

    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (tmp_path / ".car-context").exists()


def test_subprocess_runner_uses_argv_stdin_and_shell_false(tmp_path: Path, monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr("car.codex.runtime.subprocess.run", fake_run)

    result = SubprocessCodexRunner().run(
        ["codex", "login", "status"],
        cwd=tmp_path,
        stdin="",
        environment={"PATH": "test"},
        timeout_seconds=5,
    )

    assert result.exit_code == 0
    assert captured["args"][0] == ["codex", "login", "status"]
    assert captured["kwargs"]["shell"] is False
    assert captured["kwargs"]["input"] == ""


def test_runtime_source_has_no_credential_files_or_openai_api_access():
    source = Path(LocalCodexRuntime.__module__.replace(".", "/") + ".py")
    source = (Path.cwd() / source).read_text(encoding="utf-8")
    for forbidden in ("auth.json", "keyring", "openai.OpenAI", "codex login", "workspace-write"):
        assert forbidden not in source


@pytest.mark.parametrize(
    "gemini_flag", ["CAR_RUN_LIVE_GEMINI_TESTS", "CAR_RUN_LIVE_GEMINI_CODING_TESTS"]
)
def test_live_codex_gate_is_independent_from_gemini_live_flags(gemini_flag: str):
    environment = os.environ.copy()
    environment.pop("CAR_RUN_LIVE_CODEX_TESTS", None)
    environment[gemini_flag] = "1"
    live_test = Path(__file__).parent / "integration" / "test_codex_runtime_live.py"

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", str(live_test), "-q"],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        check=False,
        shell=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    assert completed.returncode in {0, 5}
    assert "1 skipped" in completed.stdout
