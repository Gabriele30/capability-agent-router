"""Offline real-Git and injected-failure tests for isolated worktree lifecycle."""

import subprocess
from pathlib import Path

from car.codex_write.models import CodexWriteFailureKind
from car.codex_write.workspace import (
    GitCommandResult,
    GitWorktreeRunner,
    IsolatedCodexWorkspace,
    IsolatedWorkspaceManager,
    WindowsAclResult,
    WindowsAclRunner,
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
    def __init__(self, results: list[GitCommandResult], events: list[str] | None = None) -> None:
        self.results = results
        self.calls: list[list[str]] = []
        self.events = events

    def run(self, args, *, cwd, timeout_seconds):
        self.calls.append(args)
        if self.events is not None and args[3:5] == ["worktree", "add"]:
            self.events.append("git-worktree-add")
        return self.results.pop(0)


class FakeAclRunner:
    def __init__(self, results: list[WindowsAclResult], events: list[str] | None = None) -> None:
        self.results = results
        self.calls: list[tuple[Path, Path, int]] = []
        self.events = events

    def run(self, executable: Path, target: Path, *, timeout_seconds: int) -> WindowsAclResult:
        self.calls.append((executable, target, timeout_seconds))
        if self.events is not None:
            self.events.append("acl-inheritance")
        return self.results.pop(0)


def _windows_manager(
    tmp_path: Path,
    git_results: list[GitCommandResult],
    acl_results: list[WindowsAclResult],
    *,
    events: list[str] | None = None,
):
    system_root = tmp_path / "Windows"
    executable = system_root / "System32" / "icacls.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    git_runner = FakeRunner(git_results, events)
    acl_runner = FakeAclRunner(acl_results, events)
    manager = IsolatedWorkspaceManager(
        git_runner,
        acl_runner=acl_runner,
        is_windows=True,
        system_root=system_root,
    )
    return manager, git_runner, acl_runner, executable


def _create_results(root: Path, *, cleanup: bool = False) -> list[GitCommandResult]:
    results = [
        GitCommandResult(exit_code=0, stdout=f"{root}\n"),
        GitCommandResult(exit_code=0, stdout="a" * 40 + "\n"),
        GitCommandResult(exit_code=0),
    ]
    if cleanup:
        results.append(GitCommandResult(exit_code=0))
    return results


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


def test_windows_acl_preparation_precedes_worktree_add_and_uses_owned_parent(tmp_path: Path):
    root = (tmp_path / "source").resolve()
    root.mkdir()
    events: list[str] = []
    manager, git_runner, acl_runner, executable = _windows_manager(
        tmp_path,
        _create_results(root, cleanup=True),
        [WindowsAclResult(exit_code=0)],
        events=events,
    )

    created = manager.create(root)

    assert created.created and created.workspace is not None
    workspace = created.workspace
    assert events.index("acl-inheritance") < events.index("git-worktree-add")
    assert len(acl_runner.calls) == 1
    acl_executable, target, timeout_seconds = acl_runner.calls[0]
    assert acl_executable == executable
    assert acl_executable.name == "icacls.exe"
    assert target == workspace.parent
    assert target.name.startswith("car-codex-worktree-")
    assert target != root and not target.is_relative_to(root)
    assert timeout_seconds == 30
    assert git_runner.calls[2][3:5] == ["worktree", "add"]

    assert manager.cleanup(workspace).removed


def test_windows_acl_failure_fails_closed_before_worktree_and_cleans_parent(tmp_path: Path):
    root = (tmp_path / "source").resolve()
    root.mkdir()
    manager, git_runner, acl_runner, _ = _windows_manager(
        tmp_path,
        _create_results(root)[:2],
        [WindowsAclResult(exit_code=1)],
    )

    failed = manager.create(root)

    assert not failed.created
    assert failed.failure_kind == CodexWriteFailureKind.WORKSPACE_SETUP_FAILED
    assert len(acl_runner.calls) == 1
    assert len(git_runner.calls) == 2
    assert not acl_runner.calls[0][1].exists()
    assert acl_runner.calls[0][1] != root


def test_windows_acl_timeout_and_missing_executable_fail_closed(tmp_path: Path):
    root = (tmp_path / "source").resolve()
    root.mkdir()
    timeout_manager, timeout_git, timeout_acl, _ = _windows_manager(
        tmp_path,
        _create_results(root)[:2],
        [WindowsAclResult(timed_out=True)],
    )

    timed_out = timeout_manager.create(root)

    assert timed_out.failure_kind == CodexWriteFailureKind.WORKSPACE_SETUP_FAILED
    assert len(timeout_acl.calls) == 1
    assert len(timeout_git.calls) == 2
    assert not timeout_acl.calls[0][1].exists()

    system_root = tmp_path / "MissingWindows"
    system_root.mkdir()
    missing_git = FakeRunner(_create_results(root)[:2])
    missing_acl = FakeAclRunner([])
    missing_manager = IsolatedWorkspaceManager(
        missing_git,
        acl_runner=missing_acl,
        is_windows=True,
        system_root=system_root,
    )
    missing = missing_manager.create(root)
    assert missing.failure_kind == CodexWriteFailureKind.WORKSPACE_SETUP_FAILED
    assert missing_acl.calls == []
    assert len(missing_git.calls) == 2


def test_windows_acl_runner_uses_only_inheritance_with_structured_subprocess(
    monkeypatch, tmp_path: Path
):
    captured: dict[str, object] = {}

    class Completed:
        returncode = 0

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        return Completed()

    executable = tmp_path / "Windows" / "System32" / "icacls.exe"
    target = tmp_path / "car-codex-worktree-owned"
    executable.parent.mkdir(parents=True)
    executable.touch()
    target.mkdir()
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = WindowsAclRunner().run(executable, target, timeout_seconds=7)

    assert result.exit_code == 0
    assert captured["args"] == [str(executable), str(target), "/inheritance:e"]
    assert captured["shell"] is False
    assert captured["cwd"] == target.parent
    command = " ".join(captured["args"])
    for forbidden in ("/grant", "/reset", "/setowner", "/t", "/c", "Everyone", "CodexSandbox"):
        assert forbidden not in command


def test_posix_workspace_lifecycle_does_not_run_windows_acl_preparation(tmp_path: Path):
    root = (tmp_path / "source").resolve()
    root.mkdir()
    git_runner = FakeRunner(_create_results(root, cleanup=True))
    acl_runner = FakeAclRunner([])
    manager = IsolatedWorkspaceManager(git_runner, acl_runner=acl_runner, is_windows=False)

    created = manager.create(root)

    assert created.created and created.workspace is not None
    assert acl_runner.calls == []
    assert git_runner.calls[2][3:5] == ["worktree", "add"]
    assert manager.cleanup(created.workspace).removed


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
    head = _git(git_repository, "rev-parse", "HEAD").strip()
    created = manager.create(git_repository, revision=head)
    assert created.workspace is not None
    workspace = created.workspace
    try:
        assert workspace.revision == _git(git_repository, "rev-parse", "HEAD").strip()
    finally:
        assert manager.cleanup(workspace).removed
    rejected = manager.create(git_repository, revision="0" * 40)
    assert rejected.failure_kind == CodexWriteFailureKind.INVALID_BASELINE


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
