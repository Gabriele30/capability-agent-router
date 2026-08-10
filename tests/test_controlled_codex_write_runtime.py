"""Offline safety tests for the isolated, controlled Codex write runtime."""

import os
import subprocess
from pathlib import Path

import pytest

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.models import (
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
)
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime import (
    CONTROLLED_WRITE_INSTRUCTION,
    ControlledCodexWriteRuntime,
    SubprocessControlledCodexRunner,
    controlled_child_environment,
)
from car.codex_write.runtime_models import (
    ControlledCodexHealthStatus,
    ControlledCodexProcessResult,
    ControlledCodexWriteRequest,
)
from car.codex_write.workspace import IsolatedWorkspaceManager
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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _policy(**values: object) -> CodexWritePolicy:
    return CodexWritePolicy(enabled=True, max_baseline_files=50, **values)


class FakeRunner:
    def __init__(
        self, results: list[ControlledCodexProcessResult], mutate_cwd: bool = False
    ) -> None:
        self.results = results
        self.mutate_cwd = mutate_cwd
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
        if self.mutate_cwd and len(self.calls) == 2:
            (cwd / "README.md").write_bytes(b"changed only in isolated workspace\n")
        return self.results.pop(0)


def _projected(source: Path):
    policy = _policy()
    captured = SourceBaselineService().capture(source, policy)
    assert captured.baseline is not None
    manager = IsolatedWorkspaceManager()
    service = BaselineProjectionService(workspace_manager=manager)
    result = service.project(source, captured.baseline, policy)
    assert result.projected_workspace is not None
    return result.projected_workspace, manager, service


def _request(workspace, task: str = "Fix synthetic marker") -> ControlledCodexWriteRequest:
    return ControlledCodexWriteRequest(
        workspace=workspace,
        task=task,
        authorized_paths=("README.md",),
        timeout_seconds=30,
    )


def _handoff() -> CodexHandoff:
    return CodexHandoff(
        task="Prior attempt failed",
        routing=RoutingHandoffSummary(
            deterministic_route=Route.GEMINI_TO_CODEX,
            final_route=Route.GEMINI_TO_CODEX,
            decision_sources=["deterministic"],
            fusion_reasons=["synthetic evidence"],
            provider_influenced_decision=False,
            deterministic_risk=0.4,
            final_risk=0.4,
        ),
        repository=RepositoryHandoffSummary(
            name="synthetic", branch="main", dirty=False, languages={}, systems=[]
        ),
        selected_files=["README.md"],
        coding_attempt=CodingAttemptSummary(provider="gemini", attempted=True, succeeded=False),
        patch_attempt=PatchAttemptSummary(),
        verification=VerificationHandoffSummary(failure_kind="check_failed"),
        escalation_reason=EscalationReason.VERIFICATION_FAILED,
        recommended_next_step="Provide a concise diagnosis.",
    )


def _ready() -> ControlledCodexProcessResult:
    return ControlledCodexProcessResult(exit_code=0, stdout="logged in")


def _runtime(manager, runner, *, executable="C:/tools/codex.CMD", policy=None, is_windows=None):
    return ControlledCodexWriteRuntime(
        workspace_manager=manager,
        runner=runner,
        which=lambda name: executable,
        policy=policy or _policy(),
        is_windows=is_windows,
    )


def test_disabled_and_unauthorized_gates_start_no_health_or_process(git_repository: Path):
    projected, manager, service = _projected(git_repository)
    try:
        disabled_runner = FakeRunner([])
        disabled = _runtime(manager, disabled_runner, policy=CodexWritePolicy())
        assert disabled.health().status == ControlledCodexHealthStatus.DISABLED
        disabled_result = disabled.execute(
            _request(projected), CodexWriteAuthorization(authorized=True)
        )
        assert disabled_result.failure_kind == CodexWriteFailureKind.DISABLED
        assert disabled_runner.calls == []

        unauthorized_runner = FakeRunner([])
        unauthorized = _runtime(manager, unauthorized_runner)
        result = unauthorized.execute(_request(projected), CodexWriteAuthorization())
        assert result.failure_kind == CodexWriteFailureKind.NOT_AUTHORIZED
        assert unauthorized_runner.calls == []
    finally:
        assert service.cleanup(projected).removed


def test_runtime_requires_workspace_owned_by_its_manager(git_repository: Path):
    projected, owner, service = _projected(git_repository)
    try:
        runner = FakeRunner([])
        runtime = _runtime(IsolatedWorkspaceManager(), runner)
        result = runtime.execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert result.failure_kind == CodexWriteFailureKind.INVALID_WORKSPACE
        assert runner.calls == []
    finally:
        assert service.cleanup(projected).removed


@pytest.mark.parametrize("executable", ["C:/tools/codex.CMD", "/usr/local/bin/codex"])
def test_controlled_runtime_uses_fixed_workspace_write_argv_and_stdin(
    git_repository: Path, executable: str
):
    projected, manager, service = _projected(git_repository)
    task = "SUPER_SENSITIVE_TASK_MARKER fix README"
    runner = FakeRunner(
        [_ready(), _ready(), ControlledCodexProcessResult(exit_code=0, stdout="Done")]
    )
    runtime = _runtime(
        manager, runner, executable=executable, is_windows=executable.endswith(".CMD")
    )
    try:
        request = _request(projected, task).model_copy(update={"handoff": _handoff()})
        result = runtime.execute(request, CodexWriteAuthorization(authorized=True))
        call = runner.calls[1]
        argv = call["argv"]
        assert result.process_succeeded and not result.changes_accepted
        expected = [executable]
        if executable.endswith(".CMD"):
            expected.extend(["-c", 'windows.sandbox="unelevated"'])
        expected.extend(
            [
                "--ask-for-approval",
                "never",
                "exec",
                "--ephemeral",
                "--sandbox",
                "workspace-write",
                "--ignore-user-config",
                "--cd",
                str(projected.workspace.path),
                CONTROLLED_WRITE_INSTRUCTION,
            ]
        )
        assert argv == expected
        if executable.endswith(".CMD"):
            assert argv[:3] == [executable, "-c", 'windows.sandbox="unelevated"']
        else:
            assert "windows.sandbox" not in " ".join(argv)
        assert argv.index("--ask-for-approval") < argv.index("exec")
        assert argv[argv.index("--cd") + 1] == str(projected.workspace.path)
        assert call["cwd"] == Path(argv[argv.index("--cd") + 1]) == projected.workspace.path
        assert str(git_repository) not in argv
        assert not any(
            flag in argv
            for flag in (
                'windows.sandbox="elevated"',
                "danger-full-access",
                "--dangerously-bypass-approvals-and-sandbox",
                "--add-dir",
                "--skip-git-repo-check",
            )
        )
        assert call["cwd"] == projected.workspace.path
        assert task in call["stdin"]
        assert "README.md" in call["stdin"]
        assert "Prior attempt failed" in call["stdin"]
        assert task not in argv
        assert "Prior attempt failed" not in argv
    finally:
        assert service.cleanup(projected).removed


def test_health_uses_resolved_executable_and_environment_is_secret_safe(
    git_repository: Path, monkeypatch
):
    projected, manager, service = _projected(git_repository)
    monkeypatch.setenv("SUPER_SECRET_CODEX_WRITE_MARKER", "do-not-pass")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("GH_TOKEN", "github-secret")
    before = {key: os.environ[key] for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "GH_TOKEN")}
    runner = FakeRunner(
        [_ready(), _ready(), ControlledCodexProcessResult(exit_code=0, stdout="Done")]
    )
    runtime = _runtime(manager, runner, executable="C:/resolved/codex.CMD")
    try:
        assert runtime.health().status == ControlledCodexHealthStatus.READY
        result = runtime.execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert result.process_succeeded
        environment = runner.calls[1]["environment"]
        for key in (
            "SUPER_SECRET_CODEX_WRITE_MARKER",
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "GH_TOKEN",
        ):
            assert key not in environment
        assert {key: os.environ[key] for key in before} == before
        assert runner.calls[0]["argv"] == ["C:/resolved/codex.CMD", "login", "status"]
    finally:
        assert service.cleanup(projected).removed


def test_child_environment_uses_one_case_insensitive_entry_per_logical_name():
    environment = controlled_child_environment(
        {
            "Path": "first-path",
            "PATH": "last-path",
            "AppData": "application-data",
            "GEMINI_API_KEY": "secret",
        }
    )
    assert environment == {"PATH": "last-path", "APPDATA": "application-data"}
    assert len({name.casefold() for name in environment}) == len(environment)


def test_timeout_nonzero_empty_output_and_missing_cli_are_structured(git_repository: Path):
    projected, manager, service = _projected(git_repository)
    try:
        cases = [
            (ControlledCodexProcessResult(timed_out=True), CodexWriteFailureKind.CODEX_TIMEOUT),
            (ControlledCodexProcessResult(exit_code=2), CodexWriteFailureKind.CODEX_NONZERO_EXIT),
            (ControlledCodexProcessResult(exit_code=0), CodexWriteFailureKind.CODEX_INVALID_OUTPUT),
        ]
        for process, expected in cases:
            runtime = _runtime(manager, FakeRunner([_ready(), process]))
            result = runtime.execute(_request(projected), CodexWriteAuthorization(authorized=True))
            assert result.attempted and not result.process_succeeded
            assert result.failure_kind == expected
        missing = ControlledCodexWriteRuntime(
            workspace_manager=manager,
            runner=FakeRunner([]),
            which=lambda name: None,
            policy=_policy(),
        ).execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert missing.failure_kind == CodexWriteFailureKind.CODEX_CLI_NOT_FOUND
        unauthenticated = _runtime(
            manager, FakeRunner([ControlledCodexProcessResult(exit_code=1)])
        ).execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert unauthenticated.failure_kind == CodexWriteFailureKind.CODEX_NOT_AUTHENTICATED
    finally:
        assert service.cleanup(projected).removed


def test_fake_runtime_change_is_confined_to_projected_workspace(git_repository: Path):
    source_before = (git_repository / "README.md").read_bytes()
    projected, manager, service = _projected(git_repository)
    runner = FakeRunner(
        [_ready(), ControlledCodexProcessResult(exit_code=0, stdout="Done")], mutate_cwd=True
    )
    runtime = _runtime(manager, runner)
    try:
        result = runtime.execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert result.process_succeeded and not result.changes_accepted
        assert (projected.workspace.path / "README.md").read_bytes() != source_before
        assert (git_repository / "README.md").read_bytes() == source_before
        assert _git(git_repository, "status", "--porcelain") == ""
    finally:
        assert service.cleanup(projected).removed


def test_subprocess_controlled_runner_uses_shell_false(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SubprocessControlledCodexRunner().run(
        ["codex", "exec"],
        cwd=tmp_path,
        stdin="input",
        environment={"PATH": "safe"},
        timeout_seconds=5,
    )
    assert result.exit_code == 0
    assert captured["args"] == (["codex", "exec"],)
    assert captured["kwargs"]["shell"] is False


def test_runtime_bounds_captured_output(git_repository: Path):
    projected, manager, service = _projected(git_repository)
    runner = FakeRunner(
        [_ready(), ControlledCodexProcessResult(exit_code=1, stdout="x" * 150, stderr="y" * 150)]
    )
    runtime = _runtime(
        manager,
        runner,
        policy=_policy(codex_max_stdout_chars=100, codex_max_stderr_chars=100),
    )
    try:
        result = runtime.execute(_request(projected), CodexWriteAuthorization(authorized=True))
        assert len(result.stdout) <= 100
        assert len(result.stderr) <= 100
        assert result.stdout.endswith("[truncated by CAR]")
        assert result.stderr.endswith("[truncated by CAR]")
    finally:
        assert service.cleanup(projected).removed


def test_write_runtime_has_no_auth_provider_or_apply_code_path():
    source = Path("car/codex_write/runtime.py").read_text(encoding="utf-8")
    for forbidden in ("auth.json", "open(.*credential", "google.genai", "PatchApplier", "requests"):
        assert forbidden not in source
