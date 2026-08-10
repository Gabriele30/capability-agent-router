"""Byte-preserving workspace snapshots without Git-based restoration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from car.execution.models import FileChange, FileChangeKind

EXCLUDED_DIRECTORIES = {
    ".git",
    ".car-context",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: Path
    files: dict[Path, bytes]

    @classmethod
    def capture(cls, root: Path) -> WorkspaceSnapshot:
        files = {
            path.relative_to(root): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and not EXCLUDED_DIRECTORIES.intersection(path.relative_to(root).parts)
        }
        return cls(root=root, files=files)

    def changes(self) -> list[FileChange]:
        current = WorkspaceSnapshot.capture(self.root).files
        changes: list[FileChange] = []
        for path in sorted(self.files.keys() | current.keys()):
            if path not in self.files:
                changes.append(FileChange(path=path.as_posix(), kind=FileChangeKind.CREATED))
            elif path not in current:
                changes.append(FileChange(path=path.as_posix(), kind=FileChangeKind.DELETED))
            elif self.files[path] != current[path]:
                changes.append(FileChange(path=path.as_posix(), kind=FileChangeKind.MODIFIED))
        return changes

    def restore(self) -> None:
        current = WorkspaceSnapshot.capture(self.root).files
        for path in current.keys() - self.files.keys():
            (self.root / path).unlink()
        for path, content in self.files.items():
            destination = self.root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)


@dataclass(frozen=True)
class TargetFileSnapshot:
    """Byte state of one CAR-authorized target before patch application."""

    path: Path
    existed: bool
    content: bytes | None


@dataclass(frozen=True)
class TargetSnapshot:
    """In-memory, target-scoped snapshot for a single safe-patch transaction."""

    root: Path
    files: dict[Path, TargetFileSnapshot]

    @classmethod
    def capture(cls, root: Path, targets: list[Path]) -> TargetSnapshot:
        """Capture every target before any mutation; no persistence or Git is used."""
        files: dict[Path, TargetFileSnapshot] = {}
        for target in targets:
            relative = target.relative_to(root)
            if target.exists():
                files[relative] = TargetFileSnapshot(
                    path=relative, existed=True, content=target.read_bytes()
                )
            else:
                files[relative] = TargetFileSnapshot(path=relative, existed=False, content=None)
        return cls(root=root, files=files)
