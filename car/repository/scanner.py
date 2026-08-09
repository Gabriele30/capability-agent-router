"""Deterministic, lightweight repository scanning."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from car.repository.git import repository_root, run_git
from car.repository.models import GitState, LanguageStats, ProjectSignals, RepositoryState

EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".c": "C",
    ".h": "C/C++ Header",
    ".cpp": "C++",
    ".hpp": "C++ Header",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cs": "C#",
    ".html": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".json": "JSON",
    ".md": "Markdown",
}

SIGNALS = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "package.json": "Node.js",
    "pnpm-lock.yaml": "Node.js",
    "yarn.lock": "Node.js",
    "package-lock.json": "Node.js",
    "Cargo.toml": "Rust",
    "go.mod": "Go",
    "CMakeLists.txt": "CMake",
    "Makefile": "Make",
    "pom.xml": "Maven",
    "build.gradle": "Gradle",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker",
    "compose.yml": "Docker",
}


class RepositoryScanError(RuntimeError):
    """Raised when a scan requires Git but cannot access a repository."""


def _status_files(root: Path) -> tuple[list[str], list[str], list[str]]:
    result = run_git(root, "status", "--porcelain=v1", "-z")
    if result is None or result.returncode != 0:
        raise RepositoryScanError("Unable to read Git working tree status.")

    modified: list[str] = []
    staged: list[str] = []
    untracked: list[str] = []
    entries = [item for item in result.stdout.split("\0") if item]
    for entry in entries:
        code, filename = entry[:2], entry[3:]
        if code == "??":
            untracked.append(filename)
            continue
        if code[0] != " ":
            staged.append(filename)
        if code[1] != " ":
            modified.append(filename)
    return modified, staged, untracked


def _tracked_files(root: Path) -> list[str]:
    result = run_git(root, "ls-files", "-z")
    if result is None or result.returncode != 0:
        raise RepositoryScanError("Unable to list Git-tracked files.")
    return [item for item in result.stdout.split("\0") if item]


def _project_signals(root: Path) -> ProjectSignals:
    files = [filename for filename in SIGNALS if (root / filename).is_file()]
    systems = list(dict.fromkeys(SIGNALS[filename] for filename in files))
    if (root / ".github" / "workflows").is_dir():
        systems.append("GitHub Actions")
    return ProjectSignals(systems=systems, files=files)


def scan_repository(directory: Path | None = None) -> RepositoryState:
    """Collect deterministic repository facts without parsing source code."""
    requested_directory = (directory or Path.cwd()).resolve()
    root = repository_root(requested_directory)
    if root is None:
        git_available = run_git(requested_directory, "--version") is not None
        if not git_available:
            raise RepositoryScanError("Git is not installed or not available on PATH.")
        raise RepositoryScanError("Current directory is not inside a Git repository.")

    branch_result = run_git(root, "branch", "--show-current")
    branch = (
        branch_result.stdout.strip() if branch_result and branch_result.returncode == 0 else None
    )
    modified, staged, untracked = _status_files(root)
    tracked_files = _tracked_files(root)
    languages = Counter(
        language
        for filename in tracked_files
        if (language := EXTENSION_LANGUAGES.get(Path(filename).suffix.lower())) is not None
    )
    return RepositoryState(
        root=root,
        name=root.name,
        git=GitState(
            available=True,
            is_repository=True,
            branch=branch or None,
            modified_files=modified,
            staged_files=staged,
            untracked_files=untracked,
        ),
        tracked_file_count=len(tracked_files),
        languages=LanguageStats(counts=dict(sorted(languages.items()))),
        project_signals=_project_signals(root),
    )
