"""Provider-safe projections and repository scope for SWE-bench instances."""

from __future__ import annotations

from pathlib import Path

from car.benchmark.swebench.models import SWEbenchInstance, SWEbenchProviderProjection
from car.coding.models import normalize_repository_relative_path
from car.repository.git import run_git

EVALUATOR_ONLY_FIELDS = frozenset({"patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"})


def project_provider_visible(instance: SWEbenchInstance) -> SWEbenchProviderProjection:
    """Project only public task metadata; solution fields have no representation."""
    return SWEbenchProviderProjection(
        instance_id=instance.instance_id,
        repo=instance.repo,
        base_commit=instance.base_commit,
        task=instance.problem_statement,
        difficulty=instance.difficulty,
        version=instance.version,
    )


def explicit_repository_scope(repository_root: Path) -> tuple[str, ...]:
    """Enumerate existing tracked regular files without deriving paths from gold data.

    The returned tuple is a benchmark-only explicit authorization input. It
    leaves the production authorization policy unchanged and fails closed for
    unavailable Git, unsafe paths, or symlinks.
    """
    root = repository_root.resolve()
    result = run_git(root, "ls-files")
    if result is None or result.returncode != 0:
        raise ValueError("SWE-bench workspace must be a Git repository with tracked files")
    paths: list[str] = []
    for raw_path in result.stdout.splitlines():
        relative = normalize_repository_relative_path(raw_path)
        target = (root / relative).resolve(strict=True)
        target.relative_to(root)
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"SWE-bench tracked path is not a regular file: {relative}")
        paths.append(relative)
    if not paths:
        raise ValueError("SWE-bench workspace has no tracked regular files")
    return tuple(paths)


def validate_base_checkout(instance: SWEbenchInstance, repository_root: Path) -> None:
    """Fail closed unless a clean checkout exactly matches the public base commit."""
    root = repository_root.resolve()
    head = run_git(root, "rev-parse", "HEAD")
    status = run_git(root, "status", "--porcelain")
    if head is None or status is None or head.returncode != 0 or status.returncode != 0:
        raise ValueError("SWE-bench workspace is not a usable Git checkout")
    if head.stdout.strip() != instance.base_commit:
        raise ValueError("SWE-bench workspace does not match the pinned base commit")
    if status.stdout.strip():
        raise ValueError("SWE-bench base checkout must be clean")
