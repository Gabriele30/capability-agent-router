"""Offline real-Git coverage for B2 working-tree projection into isolation."""

import json
import subprocess
from pathlib import Path

from car.codex_write.baseline import SourceBaselineService
from car.codex_write.models import CodexWriteFailureKind, CodexWritePolicy
from car.codex_write.projection import BaselineProjectionService


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


def _capture(source: Path):
    captured = SourceBaselineService().capture(source, _policy())
    assert captured.baseline is not None
    return captured.baseline


def _source_state(source: Path) -> tuple[bytes, str, str, str]:
    return (
        (source / "README.md").read_bytes(),
        _git(source, "status", "--porcelain=v2", "-z"),
        _git(source, "diff", "--cached", "--binary"),
        _git(source, "rev-parse", "HEAD").strip(),
    )


def _cleanup(service: BaselineProjectionService, result) -> None:
    assert result.projected_workspace is not None
    assert service.cleanup(result.projected_workspace).removed


def test_dirty_tracked_projection_uses_baseline_head_and_preserves_source(git_repository: Path):
    source = git_repository
    dirty = b"# User working tree\n"
    (source / "README.md").write_bytes(dirty)
    before = _source_state(source)
    baseline = _capture(source)
    service = BaselineProjectionService()

    result = service.project(source, baseline, _policy())

    assert result.succeeded and result.projected_workspace is not None
    workspace = result.projected_workspace.workspace
    assert workspace.revision == baseline.head_oid
    assert (workspace.path / "README.md").read_bytes() == dirty
    assert _git(workspace.path, "rev-parse", "HEAD").strip() == baseline.head_oid
    assert _git(workspace.path, "diff", "--cached", "--binary") == ""
    assert _source_state(source) == before
    _cleanup(service, result)
    assert _source_state(source) == before


def test_staged_only_and_staged_plus_unstaged_project_working_tree_bytes(git_repository: Path):
    source = git_repository
    staged = b"# Staged B\n"
    working = b"# Working C\n"
    (source / "README.md").write_bytes(staged)
    _git(source, "add", "README.md")
    baseline_b = _capture(source)
    source_index_b = _git(source, "diff", "--cached", "--binary")
    service = BaselineProjectionService()

    staged_result = service.project(source, baseline_b, _policy())
    assert staged_result.succeeded and staged_result.projected_workspace is not None
    assert (staged_result.projected_workspace.workspace.path / "README.md").read_bytes() == staged
    assert _git(source, "diff", "--cached", "--binary") == source_index_b
    _cleanup(service, staged_result)

    (source / "README.md").write_bytes(working)
    baseline_c = _capture(source)
    before = _source_state(source)
    both_result = service.project(source, baseline_c, _policy())
    assert both_result.succeeded and both_result.projected_workspace is not None
    assert (both_result.projected_workspace.workspace.path / "README.md").read_bytes() == working
    assert _source_state(source) == before
    _cleanup(service, both_result)


def test_user_deletion_and_selective_untracked_projection(git_repository: Path):
    source = git_repository
    (source / "delete_me.py").write_bytes(b"delete\n")
    _git(source, "add", "delete_me.py")
    _git(source, "commit", "-m", "deletion target")
    (source / "delete_me.py").unlink()
    untracked = b"untracked working tree bytes\n"
    (source / "new_file.py").write_bytes(untracked)
    baseline = _capture(source)
    service = BaselineProjectionService()

    default_result = service.project(source, baseline, _policy())
    assert default_result.succeeded and default_result.projected_workspace is not None
    default_root = default_result.projected_workspace.workspace.path
    assert not (default_root / "delete_me.py").exists()
    assert not (default_root / "new_file.py").exists()
    _cleanup(service, default_result)

    authorized = service.project(source, baseline, _policy(), ["new_file.py"])
    assert authorized.succeeded and authorized.projected_workspace is not None
    authorized_root = authorized.projected_workspace.workspace.path
    assert (authorized_root / "new_file.py").read_bytes() == untracked
    assert "new_file.py" in authorized.projected_untracked_paths
    assert not (authorized_root / "delete_me.py").exists()
    _cleanup(service, authorized)
    assert not (source / "delete_me.py").exists()
    assert (source / "new_file.py").read_bytes() == untracked


def test_pre_and_post_revalidation_fail_closed_and_cleanup_workspace(git_repository: Path):
    source = git_repository
    baseline = _capture(source)
    (source / "README.md").write_bytes(b"# Changed before projection\n")
    pre = BaselineProjectionService().project(source, baseline, _policy())
    assert not pre.succeeded
    assert pre.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
    assert not pre.workspace_created

    (source / "README.md").write_bytes(b"# Test\n")
    baseline = _capture(source)

    def change_source_after_copy() -> None:
        (source / "README.md").write_bytes(b"# Changed during projection\n")

    post = BaselineProjectionService(post_projection_hook=change_source_after_copy).project(
        source, baseline, _policy()
    )
    assert not post.succeeded
    assert post.failure_kind == CodexWriteFailureKind.CONCURRENT_MODIFICATION
    assert post.workspace_created
    assert post.projected_workspace is None
    assert "README.md" in post.failure_paths


def test_untracked_authorization_bounds_and_no_content_leak(git_repository: Path):
    source = git_repository
    marker = b"SUPER_SECRET_PROJECTION_MARKER"
    (source / "allowed.py").write_bytes(marker)
    baseline = _capture(source)
    service = BaselineProjectionService()

    unexpected = service.project(source, baseline, _policy(), ["not-in-baseline.py"])
    assert unexpected.failure_kind == CodexWriteFailureKind.UNEXPECTED_PATH
    assert not unexpected.workspace_created

    result = service.project(source, baseline, _policy(), ["allowed.py"])
    assert result.succeeded
    assert marker.decode() not in repr(result)
    assert marker.decode() not in json.dumps(result.metadata(), default=str)
    _cleanup(service, result)

    oversized = SourceBaselineService().capture(source, _policy(max_file_bytes=10))
    assert oversized.failure_kind == CodexWriteFailureKind.FILE_TOO_LARGE


def test_partial_projection_failure_disposes_the_isolated_workspace(
    git_repository: Path, monkeypatch
):
    source = git_repository
    (source / "second.py").write_bytes(b"base\n")
    _git(source, "add", "second.py")
    _git(source, "commit", "-m", "second file")
    (source / "README.md").write_bytes(b"dirty one\n")
    (source / "second.py").write_bytes(b"dirty two\n")
    baseline = _capture(source)
    worktrees_before = _git(source, "worktree", "list", "--porcelain")
    calls = 0

    def fail_second_copy(source_root, workspace_root, identity):
        nonlocal calls
        calls += 1
        return None if calls == 1 else CodexWriteFailureKind.PROJECTION_FAILED

    monkeypatch.setattr("car.codex_write.projection._copy_and_verify", fail_second_copy)
    result = BaselineProjectionService().project(source, baseline, _policy())

    assert not result.succeeded
    assert result.failure_kind == CodexWriteFailureKind.PROJECTION_FAILED
    assert result.workspace_created
    assert result.projected_workspace is None
    assert _git(source, "worktree", "list", "--porcelain") == worktrees_before


def test_projection_specific_file_and_byte_limits_fail_before_completion(git_repository: Path):
    source = git_repository
    (source / "second.py").write_bytes(b"base\n")
    _git(source, "add", "second.py")
    _git(source, "commit", "-m", "second file")
    (source / "README.md").write_bytes(b"abcdefgh\n")
    (source / "second.py").write_bytes(b"ijklmnop\n")
    baseline = _capture(source)
    service = BaselineProjectionService()

    too_many = service.project(source, baseline, _policy(max_projection_files=1))
    assert too_many.failure_kind == CodexWriteFailureKind.TOO_MANY_FILES
    assert too_many.workspace_created
    assert too_many.projected_workspace is None

    too_large = service.project(source, baseline, _policy(max_projection_total_bytes=10))
    assert too_large.failure_kind == CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED
    assert too_large.workspace_created
    assert too_large.projected_workspace is None


def test_projection_boundary_has_no_provider_codex_or_apply_imports():
    source = Path("car/codex_write/projection.py").read_text(encoding="utf-8")
    for forbidden in ("google.genai", "LocalCodexRuntime", "PatchApplier", "requests", "git add"):
        assert forbidden not in source
