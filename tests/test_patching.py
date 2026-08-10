"""Read-only tests for CAR-owned parsing and validation of coding proposals."""

import subprocess
from pathlib import Path

import pytest

from car.coding.models import (
    CodingExecutionPolicy,
    CodingFileContext,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    ProposedFileChange,
)
from car.patching.models import PatchValidationPolicy, PatchViolationKind
from car.patching.validation import PatchValidator
from car.providers.models import RepositoryClassificationContext
from car.router.models import Route


def context(*paths: str) -> CodingTaskContext:
    return CodingTaskContext(
        task="Update selected files",
        route=Route.GEMINI,
        repository=RepositoryClassificationContext(
            name="example", branch="main", dirty=False, languages={"Python": 1}, systems=["Python"]
        ),
        files=[CodingFileContext(path=path, content="selected\n") for path in paths],
    )


def patch(path: str = "car/a.py", *, old: str = "value = 1", new: str = "value = 2") -> str:
    return f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{old}\n+{new}\n"


def create_patch(path: str = "tests/test_new.py") -> str:
    return f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,2 @@\n+def test_new():\n+    pass\n"


def proposal(
    path: str = "car/a.py",
    operation: FileChangeOperation = FileChangeOperation.MODIFY,
    body: str | None = None,
) -> CodingProposal:
    return CodingProposal(
        summary="A safe requested change",
        changes=[ProposedFileChange(path=path, operation=operation, patch=body or patch(path))],
    )


def validate(
    root: Path,
    change: CodingProposal | None = None,
    selected: tuple[str, ...] = ("car/a.py",),
    execution_policy: CodingExecutionPolicy | None = None,
    policy: PatchValidationPolicy | None = None,
):
    return PatchValidator(policy).validate(
        change or proposal(), context(*selected), root, execution_policy
    )


def assert_violation(result, kind: PatchViolationKind) -> None:
    assert not result.valid and result.patch_set is None
    assert [violation.kind for violation in result.violations] == [kind]


def test_valid_modify_is_parsed_and_validation_never_writes(tmp_path: Path, monkeypatch):
    target = tmp_path / "car" / "a.py"
    target.parent.mkdir()
    target.write_bytes(b"value = 1\n")
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    def writes_forbidden(*args, **kwargs):
        raise AssertionError("patch validation must not write")

    monkeypatch.setattr(Path, "write_text", writes_forbidden)
    monkeypatch.setattr(Path, "write_bytes", writes_forbidden)
    monkeypatch.setattr(subprocess, "run", writes_forbidden)
    result = validate(tmp_path)

    assert result.valid and result.patch_set is not None
    parsed = result.patch_set.files[0]
    assert parsed.path == "car/a.py"
    assert parsed.operation.value == "modify" and len(parsed.hunks) == 1
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_valid_create_is_accepted_without_creating_target(tmp_path: Path):
    result = validate(
        tmp_path,
        proposal("tests/test_new.py", FileChangeOperation.CREATE, create_patch()),
        selected=(),
    )

    assert result.valid and result.patch_set is not None
    assert not (tmp_path / "tests" / "test_new.py").exists()


def test_path_mismatch_is_rejected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "a.py").write_text("value = 1\n", encoding="utf-8")
    result = validate(tmp_path, proposal("car/a.py", body=patch("car/b.py")))

    assert_violation(result, PatchViolationKind.PATH_MISMATCH)


def test_declared_operation_must_match_diff_semantics(tmp_path: Path):
    result = validate(
        tmp_path,
        proposal("new.py", FileChangeOperation.MODIFY, create_patch("new.py")),
        selected=("new.py",),
    )

    assert_violation(result, PatchViolationKind.OPERATION_MISMATCH)


def test_hidden_second_file_is_rejected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "a.py").write_text("value = 1\n", encoding="utf-8")
    body = patch("car/a.py") + patch("car/b.py")

    assert_violation(
        validate(tmp_path, proposal(body=body)), PatchViolationKind.MULTIPLE_FILES_IN_CHANGE
    )


def test_modify_must_be_selected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "b.py").write_text("value = 1\n", encoding="utf-8")

    assert_violation(
        validate(tmp_path, proposal("car/b.py"), selected=("car/a.py",)),
        PatchViolationKind.UNAUTHORIZED_FILE,
    )


def test_create_existing_and_modify_missing_targets_are_rejected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "a.py").write_text("value = 1\n", encoding="utf-8")
    existing = validate(
        tmp_path,
        proposal("car/a.py", FileChangeOperation.CREATE, create_patch("car/a.py")),
        selected=(),
    )
    missing = validate(tmp_path, proposal("car/missing.py"), selected=("car/missing.py",))

    assert_violation(existing, PatchViolationKind.TARGET_ALREADY_EXISTS)
    assert_violation(missing, PatchViolationKind.TARGET_NOT_FOUND)


def test_modify_directory_and_disabled_modify_are_rejected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    directory = validate(tmp_path, proposal("car"), selected=("car",))
    target = tmp_path / "car" / "a.py"
    target.write_text("value = 1\n", encoding="utf-8")
    disabled = validate(
        tmp_path,
        selected=("car/a.py",),
        execution_policy=CodingExecutionPolicy(allow_modify_files=False),
    )

    assert_violation(directory, PatchViolationKind.TARGET_NOT_REGULAR_FILE)
    assert_violation(disabled, PatchViolationKind.UNAUTHORIZED_FILE)


@pytest.mark.parametrize(
    "unsafe_path",
    ["../x", "a/../../x", "C:\\x", "C:/x", "/etc/x", "\\\\server\\share\\x"],
)
def test_diff_path_traversal_and_absolute_paths_are_rejected(tmp_path: Path, unsafe_path: str):
    body = f"--- a/{unsafe_path}\n+++ b/{unsafe_path}\n@@ -1 +1 @@\n-old\n+new\n"

    assert_violation(validate(tmp_path, proposal(body=body)), PatchViolationKind.PATH_ESCAPE)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_modify_symlink_is_rejected_and_outside_is_unchanged(tmp_path: Path):
    outside = tmp_path.parent / "patching-outside.py"
    outside.write_bytes(b"outside\n")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {error}")

    result = validate(tmp_path, proposal("link.py", body=patch("link.py")), selected=("link.py",))

    assert_violation(result, PatchViolationKind.SYMLINK_NOT_ALLOWED)
    assert outside.read_bytes() == b"outside\n"


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (
            "--- a/car/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-value = 1\n",
            PatchViolationKind.DELETE_NOT_SUPPORTED,
        ),
        (
            patch("car/a.py").replace("+++ b/car/a.py", "+++ b/car/b.py"),
            PatchViolationKind.RENAME_NOT_SUPPORTED,
        ),
        ("GIT binary patch\nliteral 1\n", PatchViolationKind.BINARY_PATCH_NOT_SUPPORTED),
        (
            "new file mode 100644\n" + patch("car/a.py"),
            PatchViolationKind.MODE_CHANGE_NOT_SUPPORTED,
        ),
    ],
)
def test_unsupported_diff_features_are_rejected(
    tmp_path: Path, body: str, kind: PatchViolationKind
):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "a.py").write_text("value = 1\n", encoding="utf-8")

    assert_violation(validate(tmp_path, proposal(body=body)), kind)


def test_bad_hunk_counts_and_overlapping_hunks_are_rejected(tmp_path: Path):
    (tmp_path / "car").mkdir()
    (tmp_path / "car" / "a.py").write_text("value = 1\n", encoding="utf-8")
    bad_count = "--- a/car/a.py\n+++ b/car/a.py\n@@ -1,2 +1,2 @@\n-value = 1\n+value = 2\n"
    overlap = patch("car/a.py") + "@@ -1 +1 @@\n-old\n+new\n"

    assert_violation(
        validate(tmp_path, proposal(body=bad_count)), PatchViolationKind.HUNK_COUNT_MISMATCH
    )
    assert_violation(validate(tmp_path, proposal(body=overlap)), PatchViolationKind.HUNK_OVERLAP)


def test_protected_paths_and_execution_policy_are_enforced(tmp_path: Path):
    protected = validate(
        tmp_path,
        proposal(".git/config", FileChangeOperation.CREATE, create_patch(".git/config")),
        selected=(),
    )
    car_context = validate(
        tmp_path,
        proposal(
            ".car-context/state.json",
            FileChangeOperation.CREATE,
            create_patch(".car-context/state.json"),
        ),
        selected=(),
    )
    environment = validate(
        tmp_path,
        proposal(".env.local", FileChangeOperation.CREATE, create_patch(".env.local")),
        selected=(),
    )
    disabled_create = validate(
        tmp_path,
        proposal("new.py", FileChangeOperation.CREATE, create_patch("new.py")),
        selected=(),
        execution_policy=CodingExecutionPolicy(allow_create_files=False),
    )

    assert_violation(protected, PatchViolationKind.PROTECTED_PATH)
    assert_violation(car_context, PatchViolationKind.PROTECTED_PATH)
    assert_violation(environment, PatchViolationKind.PROTECTED_PATH)
    assert_violation(disabled_create, PatchViolationKind.UNAUTHORIZED_FILE)


def test_scope_and_patch_size_limits_are_enforced(tmp_path: Path):
    first = ProposedFileChange(
        path="one.py", operation=FileChangeOperation.CREATE, patch=create_patch("one.py")
    )
    second = ProposedFileChange(
        path="two.py", operation=FileChangeOperation.CREATE, patch=create_patch("two.py")
    )
    too_many = CodingProposal(summary="Two files", changes=[first, second])
    scope = validate(tmp_path, too_many, selected=(), policy=PatchValidationPolicy(max_files=1))
    oversized = validate(
        tmp_path,
        proposal("new.py", FileChangeOperation.CREATE, create_patch("new.py")),
        selected=(),
        policy=PatchValidationPolicy(max_patch_bytes_per_file=8),
    )

    assert_violation(scope, PatchViolationKind.TOO_MANY_FILES)
    assert_violation(oversized, PatchViolationKind.PATCH_TOO_LARGE)
