"""Offline tests for exact, content-free source baseline capture."""

import os
import subprocess
from pathlib import Path

import pytest

from car.codex_write.baseline import SourceBaselineService, parse_porcelain_v2
from car.codex_write.models import CodexWriteFailureKind, CodexWritePolicy
from car.codex_write.workspace import GitCommandResult


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _policy(**values: int) -> CodexWritePolicy:
    defaults = {"max_baseline_files": 50, "max_baseline_total_bytes": 1_000_000}
    return CodexWritePolicy(**(defaults | values))


def _identity(result, path: str):
    assert result.baseline is not None
    return next(file for file in result.baseline.files if file.path == path)


def test_clean_capture_and_unchanged_revalidation_are_read_only(git_repository: Path):
    source = git_repository
    status_before = _git(source, "status", "--porcelain=v2", "-z")
    worktrees_before = _git(source, "worktree", "list", "--porcelain")
    head_before = _git(source, "rev-parse", "HEAD")
    content_before = (source / "README.md").read_bytes()
    service = SourceBaselineService()

    captured = service.capture(source, _policy())

    assert captured.captured and captured.baseline is not None
    assert captured.baseline.head_oid == head_before.strip()
    assert captured.baseline.complete_file_identities is False
    assert captured.baseline.files == []
    assert service.revalidate(source, captured.baseline, _policy()).matches
    assert (source / "README.md").read_bytes() == content_before
    assert _git(source, "status", "--porcelain=v2", "-z") == status_before
    assert _git(source, "rev-parse", "HEAD") == head_before
    assert _git(source, "worktree", "list", "--porcelain") == worktrees_before


def test_capture_represents_dirty_staged_unstaged_untracked_and_deleted(git_repository: Path):
    source = git_repository
    (source / "tracked.txt").write_text("delete me\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "tracked file")
    (source / "README.md").write_text("stage one\n", encoding="utf-8")
    _git(source, "add", "README.md")
    (source / "README.md").write_text("working tree two\n", encoding="utf-8")
    (source / "file with spaces.txt").write_text("untracked\n", encoding="utf-8")
    (source / "tracked.txt").unlink()
    status_before = _git(source, "status", "--porcelain=v2", "-z")

    captured = SourceBaselineService().capture(source, _policy())

    assert captured.captured and captured.baseline is not None
    readme = _identity(captured, "README.md")
    assert readme.staged and readme.unstaged and readme.exists
    untracked = _identity(captured, "file with spaces.txt")
    assert untracked.untracked and not untracked.tracked
    deleted = _identity(captured, "tracked.txt")
    assert deleted.tracked and not deleted.exists and deleted.unstaged
    assert _git(source, "status", "--porcelain=v2", "-z") == status_before


def test_revalidation_detects_same_size_change_new_untracked_and_staging_change(
    git_repository: Path,
):
    source = git_repository
    service = SourceBaselineService()
    captured = service.capture(source, _policy())
    assert captured.baseline is not None

    (source / "README.md").write_text("# Best\n", encoding="utf-8")
    changed = service.revalidate(source, captured.baseline, _policy())
    assert changed.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
    assert "README.md" in changed.changed_paths

    (source / "README.md").write_text("# Test\n", encoding="utf-8")
    (source / "added.txt").write_text("new\n", encoding="utf-8")
    added = service.revalidate(source, captured.baseline, _policy())
    assert added.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
    assert "added.txt" in added.changed_paths

    (source / "added.txt").unlink()
    (source / "README.md").write_text("# Best\n", encoding="utf-8")
    _git(source, "add", "README.md")
    staged = service.revalidate(source, captured.baseline, _policy())
    assert staged.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
    assert "README.md" in staged.changed_paths


def test_revalidation_detects_head_move_and_deleted_file(git_repository: Path):
    source = git_repository
    service = SourceBaselineService()
    captured = service.capture(source, _policy())
    assert captured.baseline is not None
    (source / "README.md").unlink()
    deleted = service.revalidate(source, captured.baseline, _policy())
    assert "README.md" in deleted.changed_paths

    _git(source, "checkout", "--", "README.md")
    (source / "second.txt").write_text("commit\n", encoding="utf-8")
    _git(source, "add", "second.txt")
    _git(source, "commit", "-m", "second")
    head_changed = service.revalidate(source, captured.baseline, _policy())
    assert "<HEAD>" in head_changed.changed_paths


def test_baseline_serialization_never_contains_file_content(git_repository: Path):
    marker = "SUPER_SECRET_TEST_MARKER"
    (git_repository / "secret-name-is-safe.txt").write_text(marker, encoding="utf-8")

    captured = SourceBaselineService().capture(git_repository, _policy())

    assert captured.baseline is not None
    assert marker not in captured.baseline.model_dump_json()


def test_unicode_untracked_path_is_captured_portably(git_repository: Path):
    name = "unicodé.txt"
    (git_repository / name).write_text("content\n", encoding="utf-8")

    captured = SourceBaselineService().capture(git_repository, _policy())

    assert _identity(captured, name).untracked


def test_protected_untracked_path_is_never_read_or_captured(git_repository: Path):
    marker = "SUPER_SECRET_TEST_MARKER"
    (git_repository / ".env.local").write_text(marker, encoding="utf-8")

    captured = SourceBaselineService().capture(git_repository, _policy())

    assert not captured.captured
    assert captured.failure_kind == CodexWriteFailureKind.PROTECTED_PATH
    assert marker not in captured.message


def test_bounds_and_malformed_status_fail_closed(git_repository: Path):
    (git_repository / "README.md").write_text("x" * 30, encoding="utf-8")
    oversized = SourceBaselineService().capture(git_repository, _policy(max_file_bytes=10))
    assert oversized.failure_kind == CodexWriteFailureKind.FILE_TOO_LARGE

    total = SourceBaselineService().capture(
        git_repository, _policy(max_file_bytes=100, max_baseline_total_bytes=10)
    )
    assert total.failure_kind == CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED

    (git_repository / "another.txt").write_text("new\n", encoding="utf-8")
    too_many = SourceBaselineService().capture(git_repository, _policy(max_baseline_files=1))
    assert too_many.failure_kind == CodexWriteFailureKind.TOO_MANY_FILES
    assert parse_porcelain_v2("? ordinary.txt\0")["ordinary.txt"].untracked
    try:
        parse_porcelain_v2("unexpected\0")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown porcelain records must fail closed")


def test_unsafe_symlink_and_rename_fail_closed(git_repository: Path, tmp_path: Path):
    link = git_repository / "outside-link"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")
    unsafe = SourceBaselineService().capture(git_repository, _policy())
    assert unsafe.failure_kind == CodexWriteFailureKind.UNSAFE_SYMLINK
    link.unlink()

    _git(git_repository, "mv", "README.md", "renamed.md")
    renamed = SourceBaselineService().capture(git_repository, _policy())
    assert renamed.failure_kind == CodexWriteFailureKind.UNSUPPORTED_REPOSITORY_STATE


class _FakeRunner:
    def __init__(self, results: list[GitCommandResult]) -> None:
        self.results = results

    def run(self, args, *, cwd, timeout_seconds):
        return self.results.pop(0)


def test_git_unavailable_timeout_and_unborn_head_are_structured(tmp_path: Path):
    unavailable = SourceBaselineService(_FakeRunner([GitCommandResult(unavailable=True)])).capture(
        tmp_path, _policy()
    )
    assert unavailable.failure_kind == CodexWriteFailureKind.GIT_UNAVAILABLE
    timeout = SourceBaselineService(_FakeRunner([GitCommandResult(timed_out=True)])).capture(
        tmp_path, _policy()
    )
    assert timeout.failure_kind == CodexWriteFailureKind.GIT_TIMEOUT
    root = tmp_path.resolve()
    unborn = SourceBaselineService(
        _FakeRunner(
            [GitCommandResult(exit_code=0, stdout=f"{root}\n"), GitCommandResult(exit_code=1)]
        )
    ).capture(tmp_path, _policy())
    assert unborn.failure_kind == CodexWriteFailureKind.INVALID_BASELINE


def test_baseline_boundary_has_no_provider_network_or_worktree_dependency():
    source = Path("car/codex_write/baseline.py").read_text(encoding="utf-8")
    for forbidden in ("google.genai", "LocalCodexRuntime", "requests", "worktree add"):
        assert forbidden not in source
