"""Canonical repository-relative path validation shared by CAR boundaries."""


def normalize_repository_relative_path(value: str) -> str:
    """Normalize and validate a repository-relative path without filesystem access."""
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/") or ":" in normalized:
        raise ValueError("path must be repository-relative")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(
            "path must not contain empty, current-directory, or parent-directory segments"
        )
    return normalized
