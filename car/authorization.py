"""Canonical, narrow authorization for task files and safe repository auxiliaries."""

from collections.abc import Iterable
from enum import StrEnum

from car.paths import normalize_repository_relative_path

DEFAULT_SAFE_AUXILIARY_PATHS = (
    ".gitignore",
    "README",
    "README.*",
    "docs/**",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    ".editorconfig",
)


class AuthorizedPathKind(StrEnum):
    TASK = "task"
    AUXILIARY = "auxiliary"


def render_agent_write_scope(
    task_authorized_paths: Iterable[str],
    *,
    safe_auxiliary_paths: Iterable[str] = DEFAULT_SAFE_AUXILIARY_PATHS,
) -> str:
    """Render the exact write contract shared with coding agents.

    This is behavioral guidance, not the enforcement boundary.  The same
    task paths and auxiliary policy are independently validated by CAR after
    an agent proposes or creates a filesystem delta.
    """
    task_paths = tuple(normalize_repository_relative_path(path) for path in task_authorized_paths)
    if len(task_paths) != len(set(task_paths)):
        raise ValueError("task authorization paths must be unique")
    auxiliary_paths = _validated_safe_auxiliary_paths(safe_auxiliary_paths)
    task_lines = "\n".join(f"- {path}" for path in task_paths) or "- none"
    auxiliary_lines = "\n".join(f"- {path}" for path in auxiliary_paths) or "- none"
    return (
        "WRITE SCOPE\n"
        "You may inspect and read repository files as needed to understand the task. "
        "You may modify, create, delete, or rename ONLY paths explicitly permitted below.\n\n"
        "TASK-AUTHORIZED PATHS:\n"
        f"{task_lines}\n\n"
        "OPTIONAL SAFE AUXILIARY PATHS:\n"
        f"{auxiliary_lines}\n\n"
        "Safe auxiliary paths are optional repository-maintenance paths, not required "
        "implementation scope. Everything else is read-only for this task. Tests and "
        "verification files may be read to understand expected behavior, but MUST NOT be "
        "modified unless explicitly listed in TASK-AUTHORIZED PATHS. CAR remains the "
        "authority for whether a requested create, modify, delete, or rename operation is "
        "actually permitted. CAR independently validates the complete filesystem delta; "
        "changes outside this write scope will cause the entire result to be rejected."
    )


def classify_authorized_path(
    path: str,
    task_authorized_paths: Iterable[str],
    *,
    safe_auxiliary_paths: Iterable[str] = DEFAULT_SAFE_AUXILIARY_PATHS,
) -> AuthorizedPathKind | None:
    """Classify one canonical path without filesystem access or broad globbing."""
    normalized = normalize_repository_relative_path(path)
    task_paths = {normalize_repository_relative_path(item) for item in task_authorized_paths}
    if normalized in task_paths:
        return AuthorizedPathKind.TASK
    _validated_safe_auxiliary_paths(safe_auxiliary_paths)
    if normalized in {".gitignore", "README", "CONTRIBUTING.md", "CHANGELOG.md", ".editorconfig"}:
        return AuthorizedPathKind.AUXILIARY
    if normalized.startswith("README.") and "/" not in normalized:
        return AuthorizedPathKind.AUXILIARY
    if normalized.startswith("docs/") and len(normalized) > len("docs/"):
        return AuthorizedPathKind.AUXILIARY
    return None


def _validated_safe_auxiliary_paths(paths: Iterable[str]) -> tuple[str, ...]:
    patterns = tuple(paths)
    if patterns != DEFAULT_SAFE_AUXILIARY_PATHS:
        raise ValueError("safe auxiliary policy must use CAR's fixed allowlist")
    return patterns
