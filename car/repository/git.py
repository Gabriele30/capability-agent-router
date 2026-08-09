"""Small, safe wrappers around the Git command line client."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_git(directory: Path, *args: str) -> CommandResult | None:
    """Run Git without a shell; return ``None`` when Git is unavailable."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            text=True,
        )
    except FileNotFoundError:
        return None
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def repository_root(directory: Path) -> Path | None:
    """Return the containing Git worktree, if available."""
    result = run_git(directory, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    return Path(result.stdout.strip())
