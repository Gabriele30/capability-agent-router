"""Opt-in A/B diagnostic for a real B2 workspace and the controlled write runtime."""

import getpass
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

if os.environ.get("CAR_RUN_LIVE_CODEX_RUNTIME_AB_DIAGNOSTIC") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODEX_RUNTIME_AB_DIAGNOSTIC=1 to run the controlled Codex "
        "runtime A/B diagnostic",
        allow_module_level=True,
    )

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime import (
    ControlledCodexWriteRuntime,
    SubprocessControlledCodexRunner,
    _execution_argv,
    _stdin,
    controlled_child_environment,
)
from car.codex_write.runtime_models import (
    ControlledCodexHealthStatus,
    ControlledCodexProcessResult,
    ControlledCodexWriteRequest,
)
from car.codex_write.workspace import IsolatedWorkspaceManager


@dataclass(frozen=True)
class _InvocationMetadata:
    argv: tuple[str, ...]
    cwd: Path
    environment_names: tuple[str, ...]
    stdin_sha256: str
    stdin_length: int
    timeout_seconds: float
    shell: bool = False


@dataclass(frozen=True)
class _Outcome:
    name: str
    attempted: bool
    process_succeeded: bool | None
    exit_code: int | None
    timed_out: bool
    failure_kind: str | None
    calculator_contains_plus: bool
    modified: set[str]
    created: set[str]
    deleted: set[str]
    git_status: tuple[str, ...]
    changes_accepted: bool | None


class _RecordingRunner(SubprocessControlledCodexRunner):
    """Real subprocess runner that retains only secret-safe invocation metadata."""

    def __init__(self) -> None:
        self.calls: list[_InvocationMetadata] = []

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ControlledCodexProcessResult:
        self.calls.append(
            _InvocationMetadata(
                argv=tuple(argv),
                cwd=cwd,
                environment_names=tuple(sorted(environment, key=str.casefold)),
                stdin_sha256=hashlib.sha256(stdin.encode("utf-8")).hexdigest(),
                stdin_length=len(stdin),
                timeout_seconds=timeout_seconds,
            )
        )
        return super().run(
            argv,
            cwd=cwd,
            stdin=stdin,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _outcome(
    *,
    name: str,
    before: dict[str, bytes],
    workspace: Path,
    attempted: bool,
    process_succeeded: bool | None,
    exit_code: int | None,
    timed_out: bool,
    failure_kind: str | None,
    changes_accepted: bool | None,
) -> _Outcome:
    after = _snapshot(workspace)
    before_paths, after_paths = set(before), set(after)
    return _Outcome(
        name=name,
        attempted=attempted,
        process_succeeded=process_succeeded,
        exit_code=exit_code,
        timed_out=timed_out,
        failure_kind=failure_kind,
        calculator_contains_plus="return a + b"
        in (workspace / "calculator.py").read_text(encoding="utf-8"),
        modified={path for path in before_paths & after_paths if before[path] != after[path]},
        created=after_paths - before_paths,
        deleted=before_paths - after_paths,
        git_status=tuple(_git(workspace, "status", "--porcelain").stdout.splitlines()),
        changes_accepted=changes_accepted,
    )


def _summary(outcome: _Outcome) -> str:
    """Safe live output: no env values, paths, task text, stdin, or model output."""
    return (
        f"{outcome.name}: attempted={outcome.attempted} "
        f"process_succeeded={outcome.process_succeeded} exit={outcome.exit_code} "
        f"timed_out={outcome.timed_out} failure={outcome.failure_kind} "
        f"write_succeeded={outcome.calculator_contains_plus} "
        f"modified={sorted(outcome.modified)} created={sorted(outcome.created)} "
        f"deleted={sorted(outcome.deleted)} git_status={list(outcome.git_status)} "
        f"changes_accepted={outcome.changes_accepted}"
    )


def _reset_workspace(workspace: Path, before: dict[str, bytes], baseline_head_oid: str) -> None:
    """Restore the only allowed synthetic file without recreating the B2 worktree."""
    current = _snapshot(workspace)
    changed = {path for path in set(before) & set(current) if before[path] != current[path]}
    assert changed <= {"calculator.py"}
    assert set(current) == set(before)
    if changed:
        (workspace / "calculator.py").write_bytes(before["calculator.py"])
    assert _snapshot(workspace) == before
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == baseline_head_oid
    assert _git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0
    assert _git(workspace, "status", "--porcelain").stdout == ""


def _acl_evidence(paths: dict[str, Path]) -> tuple[str, ...]:
    """Read-only, failure-only ACL evidence restricted to relevant identities."""
    if os.name != "nt":
        return ()
    principal_markers = (getpass.getuser().casefold(), "system", "administrators", "codex")
    evidence: list[str] = []
    for label, path in paths.items():
        completed = subprocess.run(
            ["icacls", str(path)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in completed.stdout.splitlines():
            if any(marker in line.casefold() for marker in principal_markers):
                evidence.append(f"{label}: {line.replace(str(path), '<target>')}")
    return tuple(evidence)


def test_exact_b2_workspace_runtime_ab_diagnostic(tmp_path: Path):
    """Live-only comparison; it reports outcomes rather than requiring either to write."""
    if os.name != "nt":
        pytest.skip("the exact B2/runtime A/B diagnostic is Windows-specific")

    source = tmp_path / "synthetic-source"
    source.mkdir()
    _git(source, "init", "-b", "main")
    calculator = source / "calculator.py"
    calculator.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    (source / "unrelated.txt").write_text("do not change\n", encoding="utf-8")
    _git(source, "add", "calculator.py", "unrelated.txt")
    _git(
        source,
        "-c",
        "user.name=CAR Runtime Diagnostic",
        "-c",
        "user.email=runtime-diagnostic@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )
    source_before = _snapshot(source)

    policy = CodexWritePolicy(enabled=True)
    authorization = CodexWriteAuthorization(authorized=True)
    manager = IsolatedWorkspaceManager()
    projection_service = BaselineProjectionService(workspace_manager=manager)
    captured = SourceBaselineService().capture(source, policy)
    assert captured.baseline is not None
    projected_result = projection_service.project(source, captured.baseline, policy)
    assert projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    workspace = projected.workspace.path
    assert (workspace / "calculator.py").read_text(encoding="utf-8").endswith("return a - b\n")
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "do not change\n"
    assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == captured.baseline.head_oid

    runner = _RecordingRunner()
    runtime = ControlledCodexWriteRuntime(
        workspace_manager=manager,
        runner=runner,
        policy=policy,
    )
    health = runtime.health()
    if health.status in {
        ControlledCodexHealthStatus.CLI_NOT_FOUND,
        ControlledCodexHealthStatus.NOT_AUTHENTICATED,
    }:
        pytest.skip(f"local Codex prerequisite unavailable: {health.status.value}")
    assert health.status == ControlledCodexHealthStatus.READY, health.detail
    assert health.executable is not None
    runner.calls.clear()

    request = ControlledCodexWriteRequest(
        workspace=projected,
        task=(
            "Modify only calculator.py. Change add so it returns a + b instead of a - b. "
            "Make no other changes. Do not install dependencies, access the network, stage, "
            "or commit."
        ),
        authorized_paths=("calculator.py",),
    )
    argv = _execution_argv(
        health.executable,
        workspace_path=workspace,
        is_windows=True,
    )
    direct_environment = controlled_child_environment(dict(os.environ))
    assert direct_environment == controlled_child_environment()
    assert argv[argv.index("--cd") + 1] == str(workspace)
    assert str(source) not in argv
    workspace_before = _snapshot(workspace)

    try:
        direct_process = runner.run(
            argv,
            cwd=workspace,
            stdin=_stdin(request),
            environment=direct_environment,
            timeout_seconds=policy.codex_write_timeout_seconds,
        )
        direct = _outcome(
            name="DIRECT_SUBPROCESS",
            before=workspace_before,
            workspace=workspace,
            attempted=True,
            process_succeeded=direct_process.exit_code == 0 and not direct_process.timed_out,
            exit_code=direct_process.exit_code,
            timed_out=direct_process.timed_out,
            failure_kind=None,
            changes_accepted=None,
        )
        print(_summary(direct))
        direct_call = runner.calls[-1]
        _reset_workspace(workspace, workspace_before, captured.baseline.head_oid)

        runtime_result = runtime.execute(request, authorization)
        runtime_execution_calls = [call for call in runner.calls if "exec" in call.argv]
        runtime_call = runtime_execution_calls[-1] if runtime_execution_calls else None
        runtime_outcome = _outcome(
            name="PRODUCTION_RUNTIME",
            before=workspace_before,
            workspace=workspace,
            attempted=runtime_result.attempted,
            process_succeeded=runtime_result.process_succeeded,
            exit_code=runtime_result.exit_code,
            timed_out=runtime_result.timed_out,
            failure_kind=(
                runtime_result.failure_kind.value
                if runtime_result.failure_kind is not None
                else None
            ),
            changes_accepted=runtime_result.changes_accepted,
        )
        print(_summary(runtime_outcome))
        assert runtime_result.changes_accepted is False
        if runtime_call is not None:
            assert runtime_call == direct_call
        else:
            pytest.fail("production runtime did not reach its exec invocation")

        if not direct.calculator_contains_plus:
            git_pointer = workspace / ".git"
            gitdir_parent = workspace.parent
            if git_pointer.is_file() and git_pointer.read_text(encoding="utf-8").startswith(
                "gitdir: "
            ):
                gitdir = (workspace / git_pointer.read_text(encoding="utf-8")[8:].strip()).resolve()
                gitdir_parent = gitdir.parent
            acl = _acl_evidence(
                {
                    "workspace": workspace,
                    "calculator": workspace / "calculator.py",
                    "workspace_parent": workspace.parent,
                    "gitdir_parent": gitdir_parent,
                }
            )
            print(f"DIRECT_SUBPROCESS_ACL_EVIDENCE={list(acl)}")
    finally:
        cleanup = projection_service.cleanup(projected)
        assert cleanup.removed, cleanup.message

    assert _snapshot(source) == source_before
