"""Real-Git offline tests for untrusted isolated Codex workspace delta validation."""

import os
import subprocess
from pathlib import Path

import pytest

from car.codex_write.baseline import SourceBaseline, SourceBaselineService
from car.codex_write.delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from car.codex_write.models import (
    CodexChangeOperation,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
)
from car.codex_write.projection import BaselineProjectionService, ProjectedIsolatedWorkspace
from car.codex_write.workspace import GitCommandResult, IsolatedWorkspaceManager


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _source_state(root: Path) -> tuple[dict[str, bytes], str, str, str, str]:
    return (
        {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        },
        _git(root, "rev-parse", "HEAD").stdout.strip(),
        _git(root, "branch", "--show-current").stdout.strip(),
        _git(root, "diff", "--cached", "--binary").stdout,
        _git(root, "status", "--porcelain").stdout,
    )


def _project(
    source: Path,
    policy: CodexWritePolicy | None = None,
    authorized_untracked_paths: tuple[str, ...] = (),
) -> tuple[
    SourceBaseline,
    ProjectedIsolatedWorkspace,
    IsolatedWorkspaceManager,
    BaselineProjectionService,
    CodexWritePolicy,
]:
    active_policy = policy or CodexWritePolicy(enabled=True)
    baseline = SourceBaselineService().capture(source, active_policy).baseline
    assert baseline is not None
    manager = IsolatedWorkspaceManager()
    projection = BaselineProjectionService(workspace_manager=manager)
    result = projection.project(source, baseline, active_policy, authorized_untracked_paths)
    assert result.succeeded and result.projected_workspace is not None
    return baseline, result.projected_workspace, manager, projection, active_policy


def _detect_and_validate(
    source: Path,
    baseline: SourceBaseline,
    projected: ProjectedIsolatedWorkspace,
    manager: IsolatedWorkspaceManager,
    policy: CodexWritePolicy,
    authorized_paths: tuple[str, ...],
):
    detected = CodexWorkspaceDeltaDetector(manager).detect(projected, baseline, policy)
    return CodexWorkspaceDeltaValidator().validate(
        detected,
        baseline,
        policy,
        CodexWriteAuthorization(authorized=True),
        authorized_paths,
        source,
    )


def test_no_change_is_detected_but_not_eligible_for_application(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md",)
        )
        assert result.detected and not result.valid
        assert result.failure_kind == CodexWriteFailureKind.NO_CHANGES
        assert result.delta is not None and result.delta.deltas == []
        assert result.validated_change_set is None
    finally:
        assert projection.cleanup(projected).removed


def test_authorized_modify_is_validated_without_writing_source(git_repository: Path):
    before = _source_state(git_repository)
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "README.md").write_text("# Codex change\n", encoding="utf-8")
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md",)
        )
        assert result.valid and result.validated_change_set is not None
        delta = result.validated_change_set.change_set.deltas[0]
        assert delta.path == "README.md" and delta.operation == CodexChangeOperation.MODIFY
        assert result.validated_change_set.source_revalidated
        assert _source_state(git_repository) == before
    finally:
        assert projection.cleanup(projected).removed


def test_authorized_create_requires_exact_new_path_authorization(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "created.py").write_text("value = 1\n", encoding="utf-8")
        valid = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("created.py",)
        )
        assert valid.valid
        assert (
            valid.validated_change_set.change_set.deltas[0].operation == CodexChangeOperation.CREATE
        )
    finally:
        assert projection.cleanup(projected).removed


def test_unauthorized_create_is_rejected(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "created.py").write_text("value = 1\n", encoding="utf-8")
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md",)
        )
        assert result.failure_kind == CodexWriteFailureKind.UNAUTHORIZED_CHANGE
        assert result.validated_change_set is None
    finally:
        assert projection.cleanup(projected).removed


def test_unauthorized_change_rejects_the_entire_delta_atomically(git_repository: Path):
    (git_repository / "other.py").write_text("before = 1\n", encoding="utf-8")
    _git(git_repository, "add", "other.py")
    _git(git_repository, "commit", "-m", "add other")
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "README.md").write_text("# authorized\n", encoding="utf-8")
        (projected.workspace.path / "other.py").write_text("after = 2\n", encoding="utf-8")
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md",)
        )
        assert not result.valid
        assert result.failure_kind == CodexWriteFailureKind.UNAUTHORIZED_CHANGE
        assert result.validated_change_set is None
        assert result.rejected_paths == ["other.py"]
    finally:
        assert projection.cleanup(projected).removed


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("delete", CodexWriteFailureKind.DELETE_NOT_ALLOWED),
        ("rename", CodexWriteFailureKind.RENAME_NOT_ALLOWED),
    ],
)
def test_delete_and_rename_are_detected_and_rejected(git_repository: Path, mutation, expected):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        readme = projected.workspace.path / "README.md"
        if mutation == "delete":
            readme.unlink()
        else:
            readme.rename(projected.workspace.path / "renamed.md")
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md", "renamed.md")
        )
        assert not result.valid
        assert result.failure_kind == expected
    finally:
        assert projection.cleanup(projected).removed


def test_protected_oversized_binary_and_count_limits_reject(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / ".env").write_text("SECRET=nope\n", encoding="utf-8")
        protected = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, (".env",)
        )
        assert protected.failure_kind == CodexWriteFailureKind.PROTECTED_PATH
    finally:
        assert projection.cleanup(projected).removed

    limited = CodexWritePolicy(enabled=True, max_file_bytes=8)
    baseline, projected, manager, projection, policy = _project(git_repository, limited)
    try:
        (projected.workspace.path / "large.py").write_bytes(b"123456789")
        oversized = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("large.py",)
        )
        assert oversized.failure_kind == CodexWriteFailureKind.FILE_TOO_LARGE
    finally:
        assert projection.cleanup(projected).removed

    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "binary.bin").write_bytes(b"\0binary")
        binary = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("binary.bin",)
        )
        assert binary.failure_kind == CodexWriteFailureKind.BINARY_NOT_ALLOWED
    finally:
        assert projection.cleanup(projected).removed


def test_delta_count_and_total_bounds_reject(git_repository: Path):
    count_policy = CodexWritePolicy(enabled=True, max_files=1)
    baseline, projected, manager, projection, policy = _project(git_repository, count_policy)
    try:
        (projected.workspace.path / "one.py").write_text("one\n", encoding="utf-8")
        (projected.workspace.path / "two.py").write_text("two\n", encoding="utf-8")
        count = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("one.py", "two.py")
        )
        assert count.failure_kind == CodexWriteFailureKind.CHANGE_LIMIT_EXCEEDED
    finally:
        assert projection.cleanup(projected).removed

    total_policy = CodexWritePolicy(enabled=True, max_projection_total_bytes=4)
    baseline, projected, manager, projection, policy = _project(git_repository, total_policy)
    try:
        (projected.workspace.path / "total.py").write_text("12345", encoding="utf-8")
        total = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("total.py",)
        )
        assert total.failure_kind == CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED
    finally:
        assert projection.cleanup(projected).removed


def test_workspace_index_and_source_concurrency_fail_closed(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "README.md").write_text("# staged\n", encoding="utf-8")
        _git(projected.workspace.path, "add", "README.md")
        detected = CodexWorkspaceDeltaDetector(manager).detect(projected, baseline, policy)
        assert detected.failure_kind == CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED
    finally:
        assert projection.cleanup(projected).removed


@pytest.mark.parametrize("integrity", ["head", "branch"])
def test_workspace_head_and_branch_integrity_fail_closed(git_repository: Path, integrity):
    baseline, projected, manager, projection, policy = _project(git_repository)

    class IntegrityRunner:
        def run(self, args, *, cwd, timeout_seconds):
            del cwd, timeout_seconds
            if "rev-parse" in args:
                return GitCommandResult(
                    exit_code=0,
                    stdout=("b" * 40 if integrity == "head" else baseline.head_oid) + "\n",
                )
            if "symbolic-ref" in args:
                return GitCommandResult(exit_code=0 if integrity == "branch" else 1)
            return GitCommandResult(exit_code=0)

    try:
        detected = CodexWorkspaceDeltaDetector(manager, runner=IntegrityRunner()).detect(
            projected, baseline, policy
        )
        assert detected.failure_kind == CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED
    finally:
        assert projection.cleanup(projected).removed

    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        (projected.workspace.path / "README.md").write_text("# Codex\n", encoding="utf-8")
        (git_repository / "README.md").write_text("# Concurrent user\n", encoding="utf-8")
        concurrent = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("README.md",)
        )
        assert concurrent.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
        assert not concurrent.valid
    finally:
        assert projection.cleanup(projected).removed


def test_dirty_and_authorized_untracked_projection_do_not_create_false_deltas(git_repository: Path):
    (git_repository / "README.md").write_text("# Dirty user content\n", encoding="utf-8")
    (git_repository / "user-note.txt").write_text("user baseline\n", encoding="utf-8")
    baseline, projected, manager, projection, policy = _project(
        git_repository, authorized_untracked_paths=("user-note.txt",)
    )
    try:
        assert (projected.workspace.path / "README.md").read_text(
            encoding="utf-8"
        ) == "# Dirty user content\n"
        assert (projected.workspace.path / "user-note.txt").read_text(
            encoding="utf-8"
        ) == "user baseline\n"
        no_change = _detect_and_validate(
            git_repository,
            baseline,
            projected,
            manager,
            policy,
            ("README.md", "user-note.txt"),
        )
        assert no_change.failure_kind == CodexWriteFailureKind.NO_CHANGES
        (projected.workspace.path / "README.md").write_text(
            "# Codex after dirty\n", encoding="utf-8"
        )
        changed = _detect_and_validate(
            git_repository,
            baseline,
            projected,
            manager,
            policy,
            ("README.md",),
        )
        assert changed.valid
        assert [delta.path for delta in changed.delta.deltas] == ["README.md"]
    finally:
        assert projection.cleanup(projected).removed


def test_symlink_delta_is_rejected_without_dereferencing(git_repository: Path):
    baseline, projected, manager, projection, policy = _project(git_repository)
    try:
        link = projected.workspace.path / "link.py"
        try:
            os.symlink("README.md", link)
        except OSError:
            pytest.skip("symlink creation is unavailable in this test environment")
        result = _detect_and_validate(
            git_repository, baseline, projected, manager, policy, ("link.py",)
        )
        assert result.failure_kind == CodexWriteFailureKind.SYMLINK_NOT_ALLOWED
    finally:
        assert projection.cleanup(projected).removed
