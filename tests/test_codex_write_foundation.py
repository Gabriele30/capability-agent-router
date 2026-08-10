"""Offline regression tests for the no-execution controlled Codex write foundation."""

import pytest
from pydantic import ValidationError

from car.codex_write.models import (
    CodexChangeOperation,
    CodexChangeSet,
    CodexFileDelta,
    CodexFileIdentity,
    CodexWorkspaceBaseline,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    baseline_matches,
    validate_change_set,
)


def _baseline() -> CodexWorkspaceBaseline:
    return CodexWorkspaceBaseline(repository_name="example")


def _delta(
    path: str = "src/example.py", operation: CodexChangeOperation = CodexChangeOperation.MODIFY
):
    before = CodexFileIdentity(path=path, sha256="a" * 64, size_bytes=10)
    after = CodexFileIdentity(path=path, sha256="b" * 64, size_bytes=12)
    return CodexFileDelta(path=path, operation=operation, before=before, after=after)


def _validate(delta: CodexFileDelta, policy: CodexWritePolicy | None = None):
    return validate_change_set(
        CodexChangeSet(baseline=_baseline(), deltas=[delta]),
        policy or CodexWritePolicy(enabled=True),
        CodexWriteAuthorization(authorized=True),
    )


def test_policy_and_authorization_are_disabled_by_default():
    result = validate_change_set(
        CodexChangeSet(baseline=_baseline(), deltas=[_delta()]),
        CodexWritePolicy(),
        CodexWriteAuthorization(),
    )
    assert result.failure_kind == CodexWriteFailureKind.DISABLED
    assert not CodexWriteAuthorization().authorized


@pytest.mark.parametrize("operation", [CodexChangeOperation.MODIFY, CodexChangeOperation.CREATE])
def test_modify_and_create_are_future_safe_operations(operation):
    assert _validate(_delta(operation=operation)).accepted


@pytest.mark.parametrize("operation", [CodexChangeOperation.DELETE, CodexChangeOperation.RENAME])
def test_delete_and_rename_are_rejected(operation):
    result = _validate(_delta(operation=operation))
    assert result.failure_kind == CodexWriteFailureKind.UNSUPPORTED_OPERATION


@pytest.mark.parametrize("path", [".git/config", ".env", ".car-context/state.json"])
def test_protected_paths_are_rejected(path):
    result = _validate(_delta(path=path))
    assert result.failure_kind == CodexWriteFailureKind.PROTECTED_PATH


@pytest.mark.parametrize("path", ["../outside.py", "C:/outside.py", "/outside.py"])
def test_traversal_and_outside_paths_cannot_be_represented(path):
    with pytest.raises(ValidationError):
        _delta(path=path)


def test_unsafe_symlink_and_limits_are_rejected():
    symlink = _delta()
    symlink.unsafe_symlink = True
    assert _validate(symlink).failure_kind == CodexWriteFailureKind.UNSAFE_SYMLINK
    oversized = _delta()
    oversized.after.size_bytes = 100
    assert _validate(oversized, CodexWritePolicy(enabled=True, max_file_bytes=20)).failure_kind == (
        CodexWriteFailureKind.FILE_TOO_LARGE
    )


def test_baseline_mismatch_represents_concurrent_user_change():
    baseline = CodexWorkspaceBaseline(
        repository_name="example",
        files=[CodexFileIdentity(path="src/example.py", sha256="a" * 64, size_bytes=10)],
    )
    observed = baseline.model_copy(deep=True)
    observed.files[0].sha256 = "b" * 64
    assert not baseline_matches(baseline, observed)
    assert CodexWriteFailureKind.CONCURRENT_MODIFICATION.value == "concurrent_modification"


def test_models_are_data_only_and_do_not_accept_environment_or_secrets():
    with pytest.raises(ValidationError):
        CodexWritePolicy(environment={"GEMINI_API_KEY": "secret"})


def test_foundation_validation_has_no_execution_side_effects(monkeypatch):
    monkeypatch.setattr(
        "car.codex.runtime.subprocess.run",
        lambda *args, **kwargs: pytest.fail("foundation must not start subprocesses"),
    )
    result = _validate(_delta())
    assert result.accepted
