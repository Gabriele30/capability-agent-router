"""Offline transaction tests for CAR-controlled safe patch application."""

from pathlib import Path

import pytest

from car.coding.models import (
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.patching.apply import SafePatchApplier
from car.patching.models import PatchApplyFailureKind
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.rollback.snapshot import TargetSnapshot
from car.router.models import Route


def context(*paths: str) -> CodingTaskContext:
    return CodingTaskContext(
        task="Apply selected changes",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="example", branch="main", dirty=True, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path=path, content="selected\n") for path in paths],
    )


def modify_patch(path: str, old: str, new: str) -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def create_patch(path: str, lines: list[str]) -> str:
    additions = "".join(f"+{line}\n" for line in lines)
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{additions}"


def proposal(*changes: ProposedFileChange) -> CodingProposal:
    return CodingProposal(summary="Apply a focused change", changes=list(changes))


def change(
    path: str, body: str, operation: FileChangeOperation = FileChangeOperation.MODIFY
) -> ProposedFileChange:
    return ProposedFileChange(path=path, operation=operation, patch=body)


def validated(root: Path, item: CodingProposal, *selected: str):
    result = PatchValidator().validate(item, context(*selected), root)
    assert result.valid and result.patch_set is not None
    return result.patch_set


def test_safe_applier_accepts_only_validated_patch_sets(tmp_path: Path):
    with pytest.raises(TypeError, match="ValidatedPatchSet"):
        SafePatchApplier().apply(tmp_path, proposal(change("a.py", modify_patch("a.py", "a", "b"))))


def test_modify_success_and_future_rollback_handle(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.succeeded and not transaction.result.rolled_back
    assert transaction.result.modified_files == ["a.py"]
    assert target.read_bytes() == b"value = 2\n"
    assert transaction.rollback()
    assert target.read_bytes() == b"value = 1\n"


def test_create_success_requires_existing_parent(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    patch_set = validated(
        tmp_path,
        proposal(
            change(
                "tests/new.py",
                create_patch("tests/new.py", ["value = 2"]),
                FileChangeOperation.CREATE,
            )
        ),
    )

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.succeeded
    assert transaction.result.created_files == ["tests/new.py"]
    assert (tmp_path / "tests" / "new.py").read_bytes() == b"value = 2\n"


def test_create_does_not_create_missing_parent_directories(tmp_path: Path):
    patch_set = validated(
        tmp_path,
        proposal(
            change(
                "missing/new.py",
                create_patch("missing/new.py", ["value = 2"]),
                FileChangeOperation.CREATE,
            )
        ),
    )

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.failure_kind == PatchApplyFailureKind.TARGET_NOT_FOUND
    assert not (tmp_path / "missing").exists()


def test_multiple_hunks_apply_against_old_content(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\ntwo\nthree\nfour\n")
    body = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-one\n+ONE\n@@ -4 +4 @@\n-four\n+FOUR\n"
    patch_set = validated(tmp_path, proposal(change("a.py", body)), "a.py")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.succeeded
    assert target.read_bytes() == b"ONE\ntwo\nthree\nFOUR\n"


def test_modify_preserves_crlf(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\r\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.succeeded
    assert target.read_bytes() == b"value = 2\r\n"


def test_modify_without_final_newline_uses_standard_newline_markers(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1")
    body = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n"
        "-value = 1\n\\ No newline at end of file\n"
        "+value = 2\n\\ No newline at end of file\n"
    )
    patch_set = validated(tmp_path, proposal(change("a.py", body)), "a.py")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.succeeded
    assert target.read_bytes() == b"value = 2"


def test_context_change_after_validation_is_never_overwritten(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )
    target.write_bytes(b"value = 99\n")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded
    assert transaction.result.failure_kind == PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH
    assert target.read_bytes() == b"value = 99\n"


def test_create_race_never_deletes_external_file(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    patch_set = validated(
        tmp_path,
        proposal(
            change(
                "tests/new.py", create_patch("tests/new.py", ["new"]), FileChangeOperation.CREATE
            )
        ),
    )
    target = tmp_path / "tests" / "new.py"
    target.write_bytes(b"external\n")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded
    assert transaction.result.failure_kind == PatchApplyFailureKind.TARGET_ALREADY_EXISTS
    assert not transaction.result.rolled_back
    assert target.read_bytes() == b"external\n"


def test_removed_target_after_validation_is_not_recreated(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )
    target.unlink()

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded
    assert transaction.result.failure_kind == PatchApplyFailureKind.TARGET_NOT_FOUND
    assert not target.exists()


def test_symlink_introduced_after_validation_is_rejected(tmp_path: Path):
    target = tmp_path / "a.py"
    outside = tmp_path.parent / "patch-apply-outside.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )
    outside.write_bytes(b"outside\n")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert transaction.result.failure_kind == PatchApplyFailureKind.SYMLINK_NOT_ALLOWED
    assert outside.read_bytes() == b"outside\n"


def test_partial_apply_rolls_back_only_car_owned_writes(tmp_path: Path):
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    unrelated = tmp_path / "notes.txt"
    first.write_bytes(b"value = 1\n")
    second.write_bytes(b"value = 1\n")
    unrelated.write_bytes(b"user notes\n")
    patch_set = validated(
        tmp_path,
        proposal(
            change("a.py", modify_patch("a.py", "value = 1", "value = 2")),
            change("b.py", modify_patch("b.py", "value = 1", "value = 2")),
        ),
        "a.py",
        "b.py",
    )
    second.write_bytes(b"external = 99\n")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded and transaction.result.rolled_back
    assert transaction.result.failure_kind == PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH
    assert first.read_bytes() == b"value = 1\n"
    assert second.read_bytes() == b"external = 99\n"
    assert unrelated.read_bytes() == b"user notes\n"


def test_created_file_is_removed_when_later_modify_fails(tmp_path: Path):
    (tmp_path / "tests").mkdir()
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(
            change(
                "tests/new.py", create_patch("tests/new.py", ["new"]), FileChangeOperation.CREATE
            ),
            change("a.py", modify_patch("a.py", "value = 1", "value = 2")),
        ),
        "a.py",
    )
    target.write_bytes(b"external = 99\n")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded and transaction.result.rolled_back
    assert not (tmp_path / "tests" / "new.py").exists()
    assert target.read_bytes() == b"external = 99\n"


def test_dirty_preexisting_content_is_restored_byte_for_byte(tmp_path: Path):
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    dirty = b"user change\r\n"
    first.write_bytes(dirty)
    second.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(
            change("a.py", modify_patch("a.py", "user change", "car change")),
            change("b.py", modify_patch("b.py", "value = 1", "value = 2")),
        ),
        "a.py",
        "b.py",
    )
    second.write_bytes(b"external\n")

    transaction = SafePatchApplier().apply(tmp_path, patch_set)

    assert not transaction.result.succeeded and transaction.result.rolled_back
    assert first.read_bytes() == dirty
    assert second.read_bytes() == b"external\n"


def test_snapshot_failure_performs_zero_writes(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(change("a.py", modify_patch("a.py", "value = 1", "value = 2"))),
        "a.py",
    )

    def failed_snapshot(root: Path, targets: list[Path]) -> TargetSnapshot:
        raise OSError("snapshot failure")

    transaction = SafePatchApplier(snapshot_factory=failed_snapshot).apply(tmp_path, patch_set)

    assert not transaction.result.attempted
    assert transaction.result.failure_kind == PatchApplyFailureKind.SNAPSHOT_FAILED
    assert target.read_bytes() == b"value = 1\n"


def test_second_write_failure_restores_first_and_second_targets(tmp_path: Path):
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_bytes(b"value = 1\n")
    second.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(
            change("a.py", modify_patch("a.py", "value = 1", "value = 2")),
            change("b.py", modify_patch("b.py", "value = 1", "value = 2")),
        ),
        "a.py",
        "b.py",
    )
    calls = []

    def flaky_write(path: Path, content: bytes) -> None:
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("simulated write failure")
        path.write_bytes(content)

    transaction = SafePatchApplier(write_bytes=flaky_write).apply(tmp_path, patch_set)

    assert transaction.result.failure_kind == PatchApplyFailureKind.WRITE_FAILED
    assert transaction.result.rolled_back
    assert first.read_bytes() == b"value = 1\n"
    assert second.read_bytes() == b"value = 1\n"


def test_rollback_failure_preserves_original_apply_failure(tmp_path: Path):
    target = tmp_path / "a.py"
    second = tmp_path / "b.py"
    target.write_bytes(b"value = 1\n")
    second.write_bytes(b"value = 1\n")
    patch_set = validated(
        tmp_path,
        proposal(
            change("a.py", modify_patch("a.py", "value = 1", "value = 2")),
            change("b.py", modify_patch("b.py", "value = 1", "value = 2")),
        ),
        "a.py",
        "b.py",
    )
    second.write_bytes(b"external\n")
    calls = []

    def rollback_fails(path: Path, content: bytes) -> None:
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("rollback failure")
        path.write_bytes(content)

    transaction = SafePatchApplier(write_bytes=rollback_fails).apply(tmp_path, patch_set)

    assert transaction.result.failure_kind == PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH
    assert transaction.result.rollback_failure_kind == PatchApplyFailureKind.ROLLBACK_FAILED
    assert not transaction.result.rolled_back
