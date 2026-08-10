"""Read-only capture and revalidation of the user-visible source baseline."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from car.coding.models import normalize_repository_relative_path

from .models import (
    CodexFileIdentity,
    CodexWorkspaceBaseline,
    CodexWriteFailureKind,
    CodexWritePolicy,
)
from .workspace import GitCommandResult, GitWorktreeRunner


class SourceBaseline(CodexWorkspaceBaseline):
    """Immutable, content-free observation of a source working tree."""

    head_oid: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    baseline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BaselineCaptureResult:
    captured: bool
    baseline: SourceBaseline | None = None
    failure_kind: CodexWriteFailureKind | None = None
    message: str = ""


@dataclass(frozen=True)
class BaselineRevalidationResult:
    matches: bool
    observed: SourceBaseline | None = None
    failure_kind: CodexWriteFailureKind | None = None
    changed_paths: tuple[str, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class _StatusEntry:
    path: str
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False
    unsupported: bool = False


class SourceBaselineService:
    """Captures source metadata without creating worktrees or mutating Git state."""

    def __init__(self, runner: GitWorktreeRunner | None = None, timeout_seconds: int = 30) -> None:
        self._runner = runner or GitWorktreeRunner()
        self._timeout_seconds = timeout_seconds

    def capture(self, repository: Path, policy: CodexWritePolicy) -> BaselineCaptureResult:
        root, head, failure = self._resolve_repository(repository)
        if failure is not None:
            return failure
        status_result = self._run(
            root,
            ["git", "-C", str(root), "status", "--porcelain=v2", "-z", "--untracked-files=all"],
        )
        if not _ok(status_result):
            return _capture_failure(
                _failure(status_result, CodexWriteFailureKind.INVALID_BASELINE), "Git status failed"
            )
        try:
            status = parse_porcelain_v2(status_result.stdout)
        except ValueError:
            return _capture_failure(
                CodexWriteFailureKind.MALFORMED_GIT_STATUS, "Git status was malformed"
            )
        if any(entry.unsupported for entry in status.values()):
            return _capture_failure(
                CodexWriteFailureKind.UNSUPPORTED_REPOSITORY_STATE,
                "rename, type-change, merge, or submodule state is unsupported",
            )
        tracked_result = self._run(root, ["git", "-C", str(root), "ls-files", "-z"])
        if not _ok(tracked_result):
            return _capture_failure(
                _failure(tracked_result, CodexWriteFailureKind.INVALID_BASELINE),
                "Git index listing failed",
            )
        try:
            tracked_paths = _nul_paths(tracked_result.stdout)
        except ValueError:
            return _capture_failure(
                CodexWriteFailureKind.MALFORMED_GIT_STATUS, "Git index listing was malformed"
            )
        paths = sorted(set(tracked_paths) | set(status))
        if len(paths) > policy.max_baseline_files:
            return _capture_failure(
                CodexWriteFailureKind.TOO_MANY_FILES, "baseline file limit exceeded"
            )
        identities: list[CodexFileIdentity] = []
        total_bytes = 0
        for path in paths:
            entry = status.get(path, _StatusEntry(path=path))
            identity_result = _identity(root, path, path in tracked_paths, entry, policy)
            if isinstance(identity_result, CodexWriteFailureKind):
                return _capture_failure(identity_result, f"cannot capture {path}")
            identity, bytes_count = identity_result
            total_bytes += bytes_count
            if total_bytes > policy.max_baseline_total_bytes:
                return _capture_failure(
                    CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED, "baseline byte limit exceeded"
                )
            identities.append(identity)
        baseline = SourceBaseline(
            repository_name=root.name,
            files=identities,
            repository_dirty=bool(status),
            staged_paths=sorted(path for path, entry in status.items() if entry.staged),
            untracked_paths=sorted(path for path, entry in status.items() if entry.untracked),
            head_oid=head,
            total_bytes=total_bytes,
            baseline_digest=_digest(head, identities),
        )
        return BaselineCaptureResult(captured=True, baseline=baseline)

    def revalidate(
        self, repository: Path, baseline: SourceBaseline, policy: CodexWritePolicy
    ) -> BaselineRevalidationResult:
        captured = self.capture(repository, policy)
        if not captured.captured or captured.baseline is None:
            return BaselineRevalidationResult(
                matches=False,
                failure_kind=captured.failure_kind,
                message=captured.message,
            )
        observed = captured.baseline
        if observed.baseline_digest == baseline.baseline_digest:
            return BaselineRevalidationResult(matches=True, observed=observed)
        return BaselineRevalidationResult(
            matches=False,
            observed=observed,
            failure_kind=CodexWriteFailureKind.CONCURRENT_MODIFICATION,
            changed_paths=_changed_paths(baseline, observed),
            message="source baseline changed after capture",
        )

    def _resolve_repository(
        self, repository: Path
    ) -> tuple[Path | None, str | None, BaselineCaptureResult | None]:
        try:
            candidate = repository.resolve(strict=True)
        except OSError:
            return (
                None,
                None,
                _capture_failure(CodexWriteFailureKind.INVALID_REPOSITORY, "path unavailable"),
            )
        if not candidate.is_dir():
            return (
                None,
                None,
                _capture_failure(
                    CodexWriteFailureKind.INVALID_REPOSITORY, "path is not a directory"
                ),
            )
        root_result = self._run(
            candidate, ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"]
        )
        if not _ok(root_result):
            return (
                None,
                None,
                _capture_failure(
                    _failure(root_result, CodexWriteFailureKind.INVALID_REPOSITORY),
                    "not a Git repository",
                ),
            )
        root = Path(root_result.stdout.strip()).resolve()
        head_result = self._run(root, ["git", "-C", str(root), "rev-parse", "--verify", "HEAD"])
        if not _ok(head_result):
            return (
                None,
                None,
                _capture_failure(
                    _failure(head_result, CodexWriteFailureKind.INVALID_BASELINE),
                    "HEAD unavailable",
                ),
            )
        return root, head_result.stdout.strip(), None

    def _run(self, cwd: Path, args: list[str]) -> GitCommandResult:
        return self._runner.run(args, cwd=cwd, timeout_seconds=self._timeout_seconds)


def parse_porcelain_v2(output: str) -> dict[str, _StatusEntry]:
    """Parse the NUL-delimited subset of porcelain v2 required for fail-closed capture."""
    records = output.split("\0")
    if records and records[-1] == "":
        records.pop()
    parsed: dict[str, _StatusEntry] = {}
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            raise ValueError("empty status record")
        marker = record[0]
        if marker == "?":
            path = _record_path(record, 1)
            parsed[path] = _StatusEntry(path=path, untracked=True)
        elif marker == "1":
            fields = record.split(" ", 8)
            if len(fields) != 9:
                raise ValueError("invalid ordinary status record")
            path = _valid_path(fields[8])
            xy = fields[1]
            if len(xy) != 2 or fields[2] != "N...":
                raise ValueError("invalid status flags")
            parsed[path] = _StatusEntry(path=path, staged=xy[0] != ".", unstaged=xy[1] != ".")
        elif marker == "2":
            fields = record.split(" ", 9)
            if len(fields) != 10 or index + 1 >= len(records):
                raise ValueError("invalid rename status record")
            path = _valid_path(fields[9])
            _valid_path(records[index + 1])
            parsed[path] = _StatusEntry(path=path, unsupported=True)
            index += 1
        elif marker in {"u", "!", "#"}:
            raise ValueError("unsupported status record")
        else:
            raise ValueError("unknown status record")
        index += 1
    return parsed


def _identity(
    root: Path,
    relative_path: str,
    tracked: bool,
    status: _StatusEntry,
    policy: CodexWritePolicy,
) -> tuple[CodexFileIdentity, int] | CodexWriteFailureKind:
    protected = _protected(relative_path, policy)
    candidate = root.joinpath(*relative_path.split("/"))
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        if not tracked:
            return CodexWriteFailureKind.INVALID_BASELINE
        return (
            CodexFileIdentity(
                path=relative_path,
                size_bytes=0,
                tracked=True,
                user_dirty=True,
                exists=False,
                staged=status.staged,
                unstaged=True,
            ),
            0,
        )
    except OSError:
        return CodexWriteFailureKind.INVALID_BASELINE
    if stat.S_ISLNK(info.st_mode):
        try:
            target = os.readlink(candidate)
        except OSError:
            return CodexWriteFailureKind.UNSAFE_SYMLINK
        if _unsafe_link(root, candidate, target):
            return CodexWriteFailureKind.UNSAFE_SYMLINK
        digest = _bytes_digest(target.encode("utf-8", errors="surrogateescape"))
        return (
            CodexFileIdentity(
                path=relative_path,
                sha256=digest,
                symlink_target_sha256=digest,
                size_bytes=len(target.encode("utf-8", errors="surrogateescape")),
                tracked=tracked,
                user_dirty=bool(status.staged or status.unstaged or status.untracked),
                is_symlink=True,
                staged=status.staged,
                unstaged=status.unstaged,
                untracked=status.untracked,
                protected=protected,
            ),
            len(target.encode("utf-8", errors="surrogateescape")),
        )
    if not stat.S_ISREG(info.st_mode):
        return CodexWriteFailureKind.UNSUPPORTED_REPOSITORY_STATE
    if info.st_size > policy.max_file_bytes:
        return CodexWriteFailureKind.FILE_TOO_LARGE
    if protected and (status.staged or status.unstaged or status.untracked):
        return CodexWriteFailureKind.PROTECTED_PATH
    digest = None if protected else _file_digest(candidate)
    return (
        CodexFileIdentity(
            path=relative_path,
            sha256=digest,
            size_bytes=info.st_size,
            tracked=tracked,
            user_dirty=bool(status.staged or status.unstaged or status.untracked),
            staged=status.staged,
            unstaged=status.unstaged,
            untracked=status.untracked,
            protected=protected,
        ),
        0 if protected else info.st_size,
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(head_oid: str, identities: list[CodexFileIdentity]) -> str:
    digest = hashlib.sha256()
    digest.update(head_oid.encode("ascii"))
    for identity in sorted(identities, key=lambda item: item.path):
        digest.update(identity.model_dump_json(exclude_none=True).encode("utf-8"))
    return digest.hexdigest()


def _changed_paths(before: SourceBaseline, after: SourceBaseline) -> tuple[str, ...]:
    before_files = {file.path: file for file in before.files}
    after_files = {file.path: file for file in after.files}
    paths = set(before_files) | set(after_files)
    changed = [path for path in paths if before_files.get(path) != after_files.get(path)]
    if before.head_oid != after.head_oid:
        changed.append("<HEAD>")
    return tuple(sorted(changed))


def _nul_paths(output: str) -> list[str]:
    records = output.split("\0")
    if records and records[-1] == "":
        records.pop()
    return [_valid_path(record) for record in records]


def _record_path(record: str, offset: int) -> str:
    if len(record) <= offset or record[offset] != " ":
        raise ValueError("invalid status path")
    return _valid_path(record[offset + 1 :])


def _valid_path(value: str) -> str:
    try:
        return normalize_repository_relative_path(value)
    except ValueError as error:
        raise ValueError("invalid repository-relative path") from error


def _protected(path: str, policy: CodexWritePolicy) -> bool:
    parts = path.split("/")
    return parts[0] in policy.protected_prefixes or any(
        part == ".env" or part.startswith(".env.") for part in parts
    )


def _unsafe_link(root: Path, path: Path, target: str) -> bool:
    candidate = Path(target) if Path(target).is_absolute() else (path.parent / target)
    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError:
        return True
    return False


def _capture_failure(kind: CodexWriteFailureKind, message: str) -> BaselineCaptureResult:
    return BaselineCaptureResult(captured=False, failure_kind=kind, message=message)


def _ok(result: GitCommandResult) -> bool:
    return not result.unavailable and not result.timed_out and result.exit_code == 0


def _failure(result: GitCommandResult, fallback: CodexWriteFailureKind) -> CodexWriteFailureKind:
    if result.unavailable:
        return CodexWriteFailureKind.GIT_UNAVAILABLE
    if result.timed_out:
        return CodexWriteFailureKind.GIT_TIMEOUT
    return fallback


def _bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
