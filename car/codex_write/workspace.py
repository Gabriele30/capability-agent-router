"""Offline Git worktree lifecycle for future controlled Codex coding isolation."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .models import CodexWriteFailureKind


@dataclass(frozen=True)
class GitCommandResult:
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    unavailable: bool = False
    timed_out: bool = False


class GitWorktreeRunner:
    """Small injectable Git boundary using structured argv and no shell."""

    def run(self, args: list[str], *, cwd: Path, timeout_seconds: int) -> GitCommandResult:
        try:
            completed = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            return GitCommandResult(completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError:
            return GitCommandResult(unavailable=True)
        except subprocess.TimeoutExpired as error:
            return GitCommandResult(
                stdout=_text(error.stdout), stderr=_text(error.stderr), timed_out=True
            )


@dataclass(frozen=True)
class IsolatedCodexWorkspace:
    """Runtime-only CAR-owned identity; it is intentionally not persisted or serialized."""

    source_root: Path
    path: Path
    parent: Path
    revision: str
    ownership_token: str


@dataclass(frozen=True)
class WorkspaceCreationResult:
    created: bool
    workspace: IsolatedCodexWorkspace | None = None
    failure_kind: CodexWriteFailureKind | None = None
    message: str = ""


@dataclass(frozen=True)
class WorkspaceCleanupResult:
    removed: bool
    failure_kind: CodexWriteFailureKind | None = None
    message: str = ""


class IsolatedWorkspaceManager:
    """Create and remove only CAR-owned detached worktrees from exact HEAD revisions."""

    def __init__(self, runner: GitWorktreeRunner | None = None, timeout_seconds: int = 30) -> None:
        self._runner = runner or GitWorktreeRunner()
        self._timeout_seconds = timeout_seconds
        self._owned: dict[str, IsolatedCodexWorkspace] = {}

    def create(self, repository: Path) -> WorkspaceCreationResult:
        root, revision, failure = self._resolve_source(repository)
        if failure is not None:
            return failure
        parent = Path(tempfile.mkdtemp(prefix="car-codex-worktree-", dir=None)).resolve()
        workspace_path = parent / "workspace"
        if _is_within(root, parent):
            parent.rmdir()
            return WorkspaceCreationResult(
                created=False,
                failure_kind=CodexWriteFailureKind.WORKSPACE_SETUP_FAILED,
                message="temporary workspace must be outside the source repository",
            )
        result = self._run(
            root,
            [
                "git",
                "-C",
                str(root),
                "worktree",
                "add",
                "--detach",
                str(workspace_path),
                revision,
            ],
        )
        if not _successful(result):
            self._discard_partial_workspace(root, workspace_path, parent)
            return WorkspaceCreationResult(
                created=False,
                failure_kind=_failure_kind(result, CodexWriteFailureKind.WORKSPACE_SETUP_FAILED),
                message="failed to create detached Git worktree",
            )
        workspace = IsolatedCodexWorkspace(
            source_root=root,
            path=workspace_path.resolve(),
            parent=parent,
            revision=revision,
            ownership_token=uuid4().hex,
        )
        self._owned[workspace.ownership_token] = workspace
        return WorkspaceCreationResult(created=True, workspace=workspace)

    def cleanup(self, workspace: IsolatedCodexWorkspace) -> WorkspaceCleanupResult:
        owned = self._owned.get(workspace.ownership_token)
        if owned != workspace:
            return WorkspaceCleanupResult(
                removed=False,
                failure_kind=CodexWriteFailureKind.WORKSPACE_CLEANUP_FAILED,
                message="workspace is not owned by this manager",
            )
        result = self._run(
            workspace.source_root,
            [
                "git",
                "-C",
                str(workspace.source_root),
                "worktree",
                "remove",
                "--force",
                str(workspace.path),
            ],
        )
        if not _successful(result):
            return WorkspaceCleanupResult(
                removed=False,
                failure_kind=_failure_kind(result, CodexWriteFailureKind.WORKSPACE_CLEANUP_FAILED),
                message="failed to remove detached Git worktree",
            )
        try:
            workspace.parent.rmdir()
        except OSError:
            return WorkspaceCleanupResult(
                removed=False,
                failure_kind=CodexWriteFailureKind.WORKSPACE_CLEANUP_FAILED,
                message="worktree removed but CAR temporary parent could not be removed",
            )
        del self._owned[workspace.ownership_token]
        return WorkspaceCleanupResult(removed=True)

    @contextmanager
    def temporary(self, repository: Path) -> Generator[IsolatedCodexWorkspace, None, None]:
        created = self.create(repository)
        if not created.created or created.workspace is None:
            raise IsolatedWorkspaceError(created.message)
        try:
            yield created.workspace
        finally:
            cleanup = self.cleanup(created.workspace)
            if not cleanup.removed:
                raise IsolatedWorkspaceError(cleanup.message)

    def _resolve_source(
        self, repository: Path
    ) -> tuple[Path | None, str | None, WorkspaceCreationResult | None]:
        try:
            candidate = repository.resolve(strict=True)
        except OSError:
            return (
                None,
                None,
                WorkspaceCreationResult(
                    created=False,
                    failure_kind=CodexWriteFailureKind.INVALID_REPOSITORY,
                    message="repository path is unavailable",
                ),
            )
        if not candidate.is_dir():
            return (
                None,
                None,
                WorkspaceCreationResult(
                    created=False,
                    failure_kind=CodexWriteFailureKind.INVALID_REPOSITORY,
                    message="repository path is not a directory",
                ),
            )
        root_result = self._run(
            candidate, ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"]
        )
        if not _successful(root_result):
            return (
                None,
                None,
                WorkspaceCreationResult(
                    created=False,
                    failure_kind=_failure_kind(
                        root_result, CodexWriteFailureKind.INVALID_REPOSITORY
                    ),
                    message="repository is not a usable Git worktree",
                ),
            )
        root = Path(root_result.stdout.strip()).resolve()
        head_result = self._run(root, ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"])
        if not _successful(head_result):
            return (
                None,
                None,
                WorkspaceCreationResult(
                    created=False,
                    failure_kind=_failure_kind(head_result, CodexWriteFailureKind.INVALID_BASELINE),
                    message="HEAD revision is unavailable",
                ),
            )
        return root, head_result.stdout.strip(), None

    def _run(self, cwd: Path, args: list[str]) -> GitCommandResult:
        return self._runner.run(args, cwd=cwd, timeout_seconds=self._timeout_seconds)

    def _discard_partial_workspace(self, root: Path, path: Path, parent: Path) -> None:
        """Best-effort cleanup for the exact CAR-owned path after a failed add."""
        if path.exists():
            self._run(
                root,
                ["git", "-C", str(root), "worktree", "remove", "--force", str(path)],
            )
        try:
            parent.rmdir()
        except OSError:
            # The failed setup is still reported as a structured failure. We never
            # recurse into an unknown path or touch the source worktree here.
            pass


class IsolatedWorkspaceError(RuntimeError):
    pass


def _successful(result: GitCommandResult) -> bool:
    return not result.unavailable and not result.timed_out and result.exit_code == 0


def _failure_kind(
    result: GitCommandResult, fallback: CodexWriteFailureKind
) -> CodexWriteFailureKind:
    if result.unavailable:
        return CodexWriteFailureKind.GIT_UNAVAILABLE
    if result.timed_out:
        return CodexWriteFailureKind.GIT_TIMEOUT
    return fallback


def _is_within(root: Path | None, path: Path) -> bool:
    if root is None:
        return False
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
