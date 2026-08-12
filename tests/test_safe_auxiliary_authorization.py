"""Focused offline coverage for the shared safe auxiliary write allowlist."""

import pytest

from car.authorization import (
    DEFAULT_SAFE_AUXILIARY_PATHS,
    AuthorizedPathKind,
    classify_authorized_path,
    render_agent_write_scope,
)
from car.codex_write.models import CodexWritePolicy
from car.coding.models import normalize_repository_relative_path
from car.patching.models import PatchValidationPolicy


@pytest.mark.parametrize(
    "path",
    (
        ".gitignore",
        "README",
        "README.md",
        "README.rst",
        "docs/foo.md",
        "docs/guides/setup.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        ".editorconfig",
    ),
)
def test_safe_auxiliary_paths_are_classified(path: str):
    assert classify_authorized_path(path, ("double.py",)) == AuthorizedPathKind.AUXILIARY


@pytest.mark.parametrize(
    "path",
    (
        "tests/test_x.py",
        "pyproject.toml",
        "requirements.txt",
        ".github/workflows/test.yml",
        "src/docs.py",
        "outside.py",
    ),
)
def test_non_auxiliary_paths_remain_outside_task_scope(path: str):
    assert classify_authorized_path(path, ("double.py",)) is None


def test_task_authorization_takes_precedence_over_auxiliary_classification():
    assert classify_authorized_path("README.md", ("README.md",)) == AuthorizedPathKind.TASK


def test_gemini_and_codex_default_to_the_same_fixed_auxiliary_policy():
    assert PatchValidationPolicy().safe_auxiliary_paths == DEFAULT_SAFE_AUXILIARY_PATHS
    assert CodexWritePolicy().safe_auxiliary_paths == DEFAULT_SAFE_AUXILIARY_PATHS


def test_shared_agent_write_scope_uses_the_validator_authorization_policy():
    scope = render_agent_write_scope(
        ("double.py",),
        safe_auxiliary_paths=CodexWritePolicy().safe_auxiliary_paths,
    )

    assert (
        "You may modify, create, delete, or rename ONLY paths explicitly permitted below." in scope
    )
    assert "TASK-AUTHORIZED PATHS:\n- double.py" in scope
    assert "OPTIONAL SAFE AUXILIARY PATHS:" in scope
    for path in DEFAULT_SAFE_AUXILIARY_PATHS:
        assert f"- {path}" in scope
    assert "Tests and verification files may be read" in scope
    assert "MUST NOT be modified unless explicitly listed in TASK-AUTHORIZED PATHS." in scope
    assert classify_authorized_path("tests/test_double.py", ("double.py",)) is None
    assert classify_authorized_path("tests/test_double.py", ("tests/test_double.py",)) == (
        AuthorizedPathKind.TASK
    )


@pytest.mark.parametrize("path", ("../outside.py", "docs/../outside.py", "/README.md"))
def test_auxiliary_matcher_rejects_noncanonical_paths(path: str):
    with pytest.raises(ValueError):
        normalize_repository_relative_path(path)
