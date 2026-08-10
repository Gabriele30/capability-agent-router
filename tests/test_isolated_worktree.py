"""Offline real-Git and injected-failure tests for isolated worktree lifecycle."""

import subprocess
from pathlib import Path

from car.codex_write.models import CodexWriteFailureKind
from car.codex_write.workspace import (
    GitCommandResult,
    GitWorktreeRunner,
    IsolatedCodexWorkspace,
    IsolatedWorkspaceManager,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _source_state(root: Path) -> tuple[bytes, bytes, bytes, str, str, str]:
    return (
        (root / "README.md").read_bytes(),
        (root / "untracked.txt").read_bytes(),
        (root / "staged.txt").read_bytes(),
        _git(root, "status", "--porcelain"),
        _git(root, "branch", "--show-current"),
        _git(root, "rev-parse", "HEAD").strip(),
    )


def test_real_detached_worktree_lifecycle_preserves_dirty_source(git_repository: Path):
    source = git_repository
    (source / "README.md").write_bytes(b"# Dirty user change\n")
    (source / "untracked.txt").write_bytes(b"user file\n")
    (source / "staged.txt").write_bytes(b"staged user file\n")
    _git(source, "add", "staged.txt")
    before = _source_state(source)
    manager = IsolatedWorkspaceManager()

    created = manager.create(source)

    assert created.created and created.workspace is not None
    workspace = created.workspace
    assert workspace.path != source and workspace.path.exists()
    assert not workspace.parent.is_relative_to(source.resolve())
    assert workspace.revision == before[5]
    assert (workspace.path / "README.md").read_text(encoding="utf-8") == "# Test\n"
    assert not (workspace.path / "untracked.txt").exists()
    assert not (workspace.path / "staged.txt").exists()
    assert not (workspace.path / ".car-context").exists()
    assert _git(workspace.path, "rev-parse", "HEAD").strip() == before[5]
    assert _source_state(source) == before

    cleanup = manager.cleanup(workspace)

    assert cleanup.removed and not workspace.path.exists()
    assert str(workspace.path) not in _git(source, "worktree", "list", "--porcelain")
    assert _source_state(source) == before


def test_context_manager_cleans_real_detached_worktree(git_repository: Path):
    manager = IsolatedWorkspaceManager()
    with manager.temporary(git_repository) as workspace:
        path = workspace.path
        assert path.exists()
    assert not path.exists()


class FakeRunner:
    def __init__(self, results: list[GitCommandResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    def run(self, args, *, cwd, timeout_seconds):
        self.calls.append(args)
        return self.results.pop(0)


def test_invalid_repository_git_unavailable_and_timeout_are_structured(tmp_path: Path):
    missing = IsolatedWorkspaceManager().create(tmp_path / "missing")
    assert missing.failure_kind == CodexWriteFailureKind.INVALID_REPOSITORY
    unavailable = IsolatedWorkspaceManager(FakeRunner([GitCommandResult(unavailable=True)])).create(
        tmp_path
    )
    assert unavailable.failure_kind == CodexWriteFailureKind.GIT_UNAVAILABLE
    timeout = IsolatedWorkspaceManager(FakeRunner([GitCommandResult(timed_out=True)])).create(
        tmp_path
    )
    assert timeout.failure_kind == CodexWriteFailureKind.GIT_TIMEOUT


def test_non_git_repository_and_setup_failure_are_structured(tmp_path: Path):
    non_git = IsolatedWorkspaceManager().create(tmp_path)
    assert non_git.failure_kind == CodexWriteFailureKind.INVALID_REPOSITORY

    root = tmp_path.resolve()
    runner = FakeRunner(
        [
            GitCommandResult(exit_code=0, stdout=f"{root}\n"),
            GitCommandResult(exit_code=0, stdout="a" * 40 + "\n"),
            GitCommandResult(exit_code=1, stderr="worktree failure"),
        ]
    )
    failed = IsolatedWorkspaceManager(runner).create(root)
    assert not failed.created
    assert failed.failure_kind == CodexWriteFailureKind.WORKSPACE_SETUP_FAILED


def test_unborn_head_and_cleanup_failure_are_structured(tmp_path: Path):
    root = tmp_path.resolve()
    runner = FakeRunner(
        [GitCommandResult(exit_code=0, stdout=f"{root}\n"), GitCommandResult(exit_code=1)]
    )
    unborn = IsolatedWorkspaceManager(runner).create(root)
    assert unborn.failure_kind == CodexWriteFailureKind.INVALID_BASELINE

    workspace = IsolatedCodexWorkspace(root, tmp_path / "work", tmp_path, "a" * 40, "owned")
    cleanup_runner = FakeRunner([GitCommandResult(exit_code=1)])
    manager = IsolatedWorkspaceManager(cleanup_runner)
    manager._owned[workspace.ownership_token] = workspace
    cleanup = manager.cleanup(workspace)
    assert cleanup.failure_kind == CodexWriteFailureKind.WORKSPACE_CLEANUP_FAILED


def test_worktree_uses_exact_head_oid(git_repository: Path):
    manager = IsolatedWorkspaceManager()
    created = manager.create(git_repository)
    assert created.workspace is not None
    workspace = created.workspace
    try:
        assert workspace.revision == _git(git_repository, "rev-parse", "HEAD").strip()
    finally:
        assert manager.cleanup(workspace).removed


def test_workspace_boundary_does_not_import_providers_or_codex_runtime():
    source = Path("car/codex_write/workspace.py").read_text(encoding="utf-8")
    assert "google.genai" not in source
    assert "LocalCodexRuntime" not in source


def test_git_runner_uses_structured_argv_and_disables_shell(monkeypatch, tmp_path: Path):
    received: dict[str, object] = {}

    class Completed:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(args, **kwargs):
        received["args"] = args
        received.update(kwargs)
        return Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = GitWorktreeRunner().run(["git", "status"], cwd=tmp_path, timeout_seconds=5)

    assert result.exit_code == 0
    assert received["args"] == ["git", "status"]
    assert received["shell"] is False
    assert received["cwd"] == tmp_path
