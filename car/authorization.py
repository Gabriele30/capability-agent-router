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
    patterns = tuple(safe_auxiliary_paths)
    if patterns != DEFAULT_SAFE_AUXILIARY_PATHS:
        raise ValueError("safe auxiliary policy must use CAR's fixed allowlist")
    if normalized in {".gitignore", "README", "CONTRIBUTING.md", "CHANGELOG.md", ".editorconfig"}:
        return AuthorizedPathKind.AUXILIARY
    if normalized.startswith("README.") and "/" not in normalized:
        return AuthorizedPathKind.AUXILIARY
    if normalized.startswith("docs/") and len(normalized) > len("docs/"):
        return AuthorizedPathKind.AUXILIARY
    return None
