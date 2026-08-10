"""Explicit opt-in validation for real Codex workspace-write in disposable isolation."""

import os

import pytest

if os.environ.get("CAR_RUN_LIVE_CODEX_WRITE_TESTS") != "1":
    pytest.skip(
        "set CAR_RUN_LIVE_CODEX_WRITE_TESTS=1 to run real Codex workspace-write validation",
        allow_module_level=True,
    )

import subprocess
from pathlib import Path

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.models import CodexWriteAuthorization, CodexWritePolicy
from car.codex_write.projection import BaselineProjectionService
from car.codex_write.runtime import ControlledCodexWriteRuntime
from car.codex_write.runtime_models import ControlledCodexHealthStatus, ControlledCodexWriteRequest
from car.codex_write.workspace import IsolatedWorkspaceManager


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


def _change_sets(
    before: dict[str, bytes], after: dict[str, bytes]
) -> tuple[set[str], set[str], set[str]]:
    before_paths = set(before)
    after_paths = set(after)
    return (
        {path for path in before_paths & after_paths if before[path] != after[path]},
        after_paths - before_paths,
        before_paths - after_paths,
    )


def test_real_controlled_codex_workspace_write_is_confined(tmp_path: Path, monkeypatch):
    """Live-only: filesystem assertions are test-local, not 5E4 delta acceptance."""
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
        "user.name=CAR Live Test",
        "-c",
        "user.email=live-test@example.invalid",
        "commit",
        "-m",
        "synthetic baseline",
    )
    monkeypatch.setenv("SUPER_SECRET_CODEX_WRITE_LIVE_MARKER", "synthetic-only")
    policy = CodexWritePolicy(enabled=True)
    authorization = CodexWriteAuthorization(authorized=True)
    manager = IsolatedWorkspaceManager()
    runtime = ControlledCodexWriteRuntime(workspace_manager=manager, policy=policy)
    health = runtime.health()
    if health.status in {
        ControlledCodexHealthStatus.CLI_NOT_FOUND,
        ControlledCodexHealthStatus.NOT_AUTHENTICATED,
    }:
        pytest.skip(f"local Codex prerequisite unavailable: {health.status.value}")
    assert health.status == ControlledCodexHealthStatus.READY, health.detail

    source_before = _snapshot(source)
    source_status = _git(source, "status", "--porcelain").stdout
    source_head = _git(source, "rev-parse", "HEAD").stdout.strip()
    source_branch = _git(source, "branch", "--show-current").stdout.strip()
    source_index = _git(source, "diff", "--cached", "--binary").stdout
    captured = SourceBaselineService().capture(source, policy)
    assert captured.captured and captured.baseline is not None
    baseline = captured.baseline
    projection_service = BaselineProjectionService(workspace_manager=manager)
    projected_result = projection_service.project(source, baseline, policy)
    assert projected_result.succeeded and projected_result.projected_workspace is not None
    projected = projected_result.projected_workspace
    workspace = projected.workspace.path
    assert (workspace / "calculator.py").read_bytes() == calculator.read_bytes()
    workspace_before = _snapshot(workspace)
    workspace_head = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    assert workspace_head == baseline.head_oid

    request = ControlledCodexWriteRequest(
        workspace=projected,
        task=(
            "Modify only calculator.py. Change add so it returns a + b instead of a - b. "
            "Make the smallest possible change. Do not create, delete, or rename files. "
            "Do not stage, commit, create branches, install dependencies, access the network, "
            "or run tests."
        ),
        authorized_paths=("calculator.py",),
    )
    try:
        result = runtime.execute(request, authorization)
        diagnostics = (
            f"failure={result.failure_kind} exit={result.exit_code} timeout={result.timed_out} "
            f"stdout={result.stdout[-500:]!r} stderr={result.stderr[-500:]!r}"
        )
        assert result.attempted and result.process_succeeded, diagnostics
        assert result.final_message and len(result.final_message) <= policy.codex_max_stdout_chars
        assert len(result.stdout) <= policy.codex_max_stdout_chars
        assert len(result.stderr) <= policy.codex_max_stderr_chars
        assert not result.changes_accepted

        workspace_after = _snapshot(workspace)
        modified, created, deleted = _change_sets(workspace_before, workspace_after)
        assert modified == {"calculator.py"}, (modified, created, deleted)
        assert not created and not deleted
        assert "return a + b" in (workspace / "calculator.py").read_text(encoding="utf-8")
        assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "do not change\n"
        assert _git(workspace, "rev-parse", "HEAD").stdout.strip() == baseline.head_oid
        assert _git(workspace, "diff", "--cached", "--quiet", check=False).returncode == 0
        assert _git(workspace, "symbolic-ref", "-q", "HEAD", check=False).returncode != 0
        assert "calculator.py" in _git(workspace, "status", "--porcelain").stdout

        assert _snapshot(source) == source_before
        assert _git(source, "status", "--porcelain").stdout == source_status == ""
        assert _git(source, "rev-parse", "HEAD").stdout.strip() == source_head
        assert _git(source, "branch", "--show-current").stdout.strip() == source_branch
        assert _git(source, "diff", "--cached", "--binary").stdout == source_index == ""
        assert not (source / ".car-context").exists()
        assert not (workspace / ".car-context").exists()
    finally:
        cleanup = projection_service.cleanup(projected)
        assert cleanup.removed, cleanup.message
    assert not workspace.exists()
    assert str(workspace) not in _git(source, "worktree", "list", "--porcelain").stdout
    assert _snapshot(source) == source_before
