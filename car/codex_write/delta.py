"""Read-only detection and validation of untrusted changes in a projected Codex workspace."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from car.coding.models import normalize_repository_relative_path

from .baseline import SourceBaseline, SourceBaselineService
from .models import (
    CodexChangeOperation,
    CodexChangeSet,
    CodexFileDelta,
    CodexFileIdentity,
    CodexWorkspaceDelta,
    CodexWorkspaceDeltaValidationResult,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    ValidatedCodexChangeSet,
)
from .projection import ProjectedIsolatedWorkspace
from .workspace import GitCommandResult, GitWorktreeRunner, IsolatedWorkspaceManager

_CHUNK_SIZE = 64 * 1024
_BINARY_SAMPLE_SIZE = 8 * 1024


@dataclass(frozen=True)
class _ObservedPath:
    identity: CodexFileIdentity | None = None
    unsupported_type: bool = False


class CodexWorkspaceDeltaDetector:
    """Observe a CAR-owned isolated workspace without applying any source change."""

    def __init__(
        self,
        workspace_manager: IsolatedWorkspaceManager,
        runner: GitWorktreeRunner | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._runner = runner or GitWorktreeRunner()
        self._timeout_seconds = timeout_seconds

    def detect(
        self,
        projected: ProjectedIsolatedWorkspace,
        baseline: SourceBaseline,
        policy: CodexWritePolicy,
    ) -> CodexWorkspaceDeltaValidationResult:
        workspace = projected.workspace
        if not self._workspace_manager.owns(workspace):
            return _reject(
                CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED, "workspace is not owned"
            )
        if (
            projected.baseline_digest != baseline.baseline_digest
            or projected.baseline_head_oid != baseline.head_oid
        ):
            return _reject(
                CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED, "baseline metadata mismatch"
            )
        integrity = self._verify_workspace_integrity(projected)
        if integrity is not None:
            return _reject(CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED, integrity)
        expected = _projected_identities(baseline, projected)
        scanned = _scan_workspace(workspace.path, policy)
        if isinstance(scanned, CodexWriteFailureKind):
            return _reject(scanned, "workspace filesystem could not be observed")
        observed, case_ambiguous = scanned
        if case_ambiguous:
            return _reject(CodexWriteFailureKind.UNEXPECTED_PATH, "case-ambiguous workspace paths")
        deltas = _deltas(expected, observed)
        status_failure = self._verify_machine_status(workspace.path)
        if status_failure is not None:
            return _reject(status_failure, "workspace Git status is unsupported")
        delta = CodexWorkspaceDelta(
            baseline_digest=baseline.baseline_digest,
            baseline_head_oid=baseline.head_oid,
            deltas=deltas,
            changed_paths=sorted(item.path for item in deltas),
            operation_counts=_operation_counts(deltas),
        )
        return CodexWorkspaceDeltaValidationResult(
            detected=True,
            valid=False,
            delta=delta,
            source_revalidated=False,
            workspace_integrity_valid=True,
            message="untrusted isolated workspace delta detected",
        )

    def _verify_workspace_integrity(self, projected: ProjectedIsolatedWorkspace) -> str | None:
        workspace = projected.workspace.path
        git_pointer = workspace / ".git"
        try:
            if not stat.S_ISREG(git_pointer.lstat().st_mode):
                return "linked-worktree Git pointer is invalid"
        except OSError:
            return "linked-worktree Git pointer is unavailable"
        head = self._run(workspace, ["git", "-C", str(workspace), "rev-parse", "HEAD"])
        if not _ok(head) or head.stdout.strip() != projected.baseline_head_oid:
            return "workspace HEAD changed"
        branch = self._run(workspace, ["git", "-C", str(workspace), "symbolic-ref", "-q", "HEAD"])
        if branch.exit_code == 0 or branch.unavailable or branch.timed_out:
            return "workspace is not detached"
        index = self._run(workspace, ["git", "-C", str(workspace), "diff", "--cached", "--quiet"])
        if index.exit_code != 0 or index.unavailable or index.timed_out:
            return "workspace index changed"
        return None

    def _verify_machine_status(self, workspace: Path) -> CodexWriteFailureKind | None:
        result = self._run(
            workspace,
            [
                "git",
                "-C",
                str(workspace),
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ],
        )
        if not _ok(result):
            return CodexWriteFailureKind.DELTA_DETECTION_FAILED
        records = [record for record in result.stdout.split("\0") if record]
        if any(record.startswith("2 ") for record in records):
            return CodexWriteFailureKind.RENAME_NOT_ALLOWED
        if any(record.startswith(("u ", "!", "#")) for record in records):
            return CodexWriteFailureKind.WORKSPACE_INTEGRITY_FAILED
        names = self._run(
            workspace,
            ["git", "-C", str(workspace), "diff", "--name-status", "-z", "-M"],
        )
        if not _ok(names):
            return CodexWriteFailureKind.DELTA_DETECTION_FAILED
        records = [record for record in names.stdout.split("\0") if record]
        index = 0
        while index < len(records):
            status = records[index]
            if status.startswith("R"):
                return CodexWriteFailureKind.RENAME_NOT_ALLOWED
            index += 3 if status.startswith(("R", "C")) else 2
        return None

    def _run(self, cwd: Path, args: list[str]) -> GitCommandResult:
        return self._runner.run(args, cwd=cwd, timeout_seconds=self._timeout_seconds)


class CodexWorkspaceDeltaValidator:
    """Validate an observed delta atomically; it never writes the source repository."""

    def __init__(self, baseline_service: SourceBaselineService | None = None) -> None:
        self._baseline_service = baseline_service or SourceBaselineService()

    def validate(
        self,
        detected: CodexWorkspaceDeltaValidationResult,
        baseline: SourceBaseline,
        policy: CodexWritePolicy,
        authorization: CodexWriteAuthorization,
        authorized_paths: Iterable[str],
        source_repository: Path,
    ) -> CodexWorkspaceDeltaValidationResult:
        if not detected.detected or detected.delta is None:
            return detected
        revalidated = self._baseline_service.revalidate(source_repository, baseline, policy)
        if not revalidated.matches:
            return _reject(
                CodexWriteFailureKind.CONCURRENT_MODIFICATION,
                "source baseline changed after projection",
                revalidated.changed_paths,
                delta=detected.delta,
                source_revalidated=False,
                workspace_integrity_valid=detected.workspace_integrity_valid,
            )
        if not policy.enabled:
            return _reject(
                CodexWriteFailureKind.DISABLED,
                "controlled Codex writing is disabled",
                delta=detected.delta,
                source_revalidated=True,
                workspace_integrity_valid=True,
            )
        if not authorization.authorized:
            return _reject(
                CodexWriteFailureKind.NOT_AUTHORIZED,
                "explicit authorization is required",
                delta=detected.delta,
                source_revalidated=True,
                workspace_integrity_valid=True,
            )
        allowed = _authorized_paths(authorized_paths)
        if allowed is None:
            return _reject(
                CodexWriteFailureKind.UNAUTHORIZED_CHANGE,
                "authorization paths are invalid",
                delta=detected.delta,
                source_revalidated=True,
                workspace_integrity_valid=True,
            )
        deltas = detected.delta.deltas
        if not deltas:
            return _reject(
                CodexWriteFailureKind.NO_CHANGES,
                "no workspace changes were detected",
                delta=detected.delta,
                source_revalidated=True,
                workspace_integrity_valid=True,
            )
        if len(deltas) > policy.max_files:
            return _reject(
                CodexWriteFailureKind.CHANGE_LIMIT_EXCEEDED,
                "change count exceeds policy",
                [delta.path for delta in deltas],
                detected.delta,
                True,
                True,
            )
        total_bytes = 0
        for delta in deltas:
            failure = _validate_delta(delta, policy, allowed)
            if failure is not None:
                return _reject(
                    failure,
                    "isolated change is not authorized or supported",
                    [delta.path],
                    detected.delta,
                    True,
                    True,
                )
            if delta.after is not None:
                total_bytes += delta.after.size_bytes
        if total_bytes > policy.max_projection_total_bytes:
            return _reject(
                CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED,
                "total change bytes exceed policy",
                [delta.path for delta in deltas],
                detected.delta,
                True,
                True,
            )
        change_set = CodexChangeSet(baseline=baseline, deltas=deltas)
        return CodexWorkspaceDeltaValidationResult(
            detected=True,
            valid=True,
            delta=detected.delta,
            validated_change_set=ValidatedCodexChangeSet(
                change_set=change_set,
                baseline_digest=baseline.baseline_digest,
            ),
            source_revalidated=True,
            workspace_integrity_valid=True,
            message="isolated change set validated for future application",
        )


def _projected_identities(
    baseline: SourceBaseline, projected: ProjectedIsolatedWorkspace
) -> dict[str, CodexFileIdentity]:
    projected_untracked = set(projected.untracked_paths)
    return {
        identity.path: identity
        for identity in baseline.files
        if identity.tracked or (identity.untracked and identity.path in projected_untracked)
    }


def _scan_workspace(
    root: Path, policy: CodexWritePolicy
) -> tuple[dict[str, _ObservedPath], bool] | CodexWriteFailureKind:
    observed: dict[str, _ObservedPath] = {}
    casefolded: set[str] = set()

    def visit(directory: Path) -> CodexWriteFailureKind | None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return CodexWriteFailureKind.DELTA_DETECTION_FAILED
        for entry in entries:
            if directory == root and entry.name == ".git":
                continue
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                normalized = normalize_repository_relative_path(relative)
            except ValueError:
                return CodexWriteFailureKind.UNEXPECTED_PATH
            lowered = normalized.casefold()
            if lowered in casefolded:
                return CodexWriteFailureKind.UNEXPECTED_PATH
            casefolded.add(lowered)
            try:
                info = path.lstat()
            except OSError:
                return CodexWriteFailureKind.DELTA_DETECTION_FAILED
            if stat.S_ISDIR(info.st_mode):
                failure = visit(path)
                if failure is not None:
                    return failure
                continue
            if stat.S_ISLNK(info.st_mode):
                observed[normalized] = _ObservedPath(
                    identity=CodexFileIdentity(
                        path=normalized,
                        size_bytes=0,
                        is_symlink=True,
                    )
                )
                continue
            if not stat.S_ISREG(info.st_mode):
                observed[normalized] = _ObservedPath(unsupported_type=True)
                continue
            if info.st_size > policy.max_file_bytes:
                observed[normalized] = _ObservedPath(
                    identity=CodexFileIdentity(path=normalized, size_bytes=info.st_size)
                )
                continue
            protected = _protected(normalized, policy)
            identity = CodexFileIdentity(
                path=normalized,
                sha256=None if protected else _file_digest(path),
                size_bytes=info.st_size,
                is_binary=False if protected else _is_binary(path),
                protected=protected,
            )
            observed[normalized] = _ObservedPath(identity=identity)
        return None

    failure = visit(root)
    if failure is not None:
        return failure
    return observed, False


def _deltas(
    expected: dict[str, CodexFileIdentity], observed: dict[str, _ObservedPath]
) -> list[CodexFileDelta]:
    deltas: list[CodexFileDelta] = []
    for path in sorted(set(expected) | set(observed)):
        before = expected.get(path)
        after_observed = observed.get(path)
        after = after_observed.identity if after_observed is not None else None
        unsafe = bool(
            after_observed and (after_observed.unsupported_type or after and after.is_symlink)
        )
        if before is None and after is not None:
            deltas.append(
                CodexFileDelta(
                    path=path,
                    operation=CodexChangeOperation.CREATE,
                    before=None,
                    after=after,
                    unsafe_symlink=unsafe,
                )
            )
        elif before is not None and before.exists and after is None:
            deltas.append(
                CodexFileDelta(
                    path=path,
                    operation=CodexChangeOperation.DELETE,
                    before=before,
                    after=None,
                    unsafe_symlink=unsafe,
                )
            )
        elif before is not None and not before.exists and after is not None:
            deltas.append(
                CodexFileDelta(
                    path=path,
                    operation=CodexChangeOperation.CREATE,
                    before=before,
                    after=after,
                    unsafe_symlink=unsafe,
                )
            )
        elif before is not None and after is not None and _identity_changed(before, after):
            deltas.append(
                CodexFileDelta(
                    path=path,
                    operation=CodexChangeOperation.MODIFY,
                    before=before,
                    after=after,
                    unsafe_symlink=unsafe,
                )
            )
    return _coalesce_renames(deltas)


def _coalesce_renames(deltas: list[CodexFileDelta]) -> list[CodexFileDelta]:
    """Treat an exact delete/create content move as a rename so it cannot be permitted."""
    remaining = list(deltas)
    replacements: list[CodexFileDelta] = []
    for deleted in [item for item in deltas if item.operation == CodexChangeOperation.DELETE]:
        if deleted not in remaining or deleted.before is None or deleted.before.sha256 is None:
            continue
        created = next(
            (
                item
                for item in remaining
                if item.operation == CodexChangeOperation.CREATE
                and item.after is not None
                and item.after.sha256 == deleted.before.sha256
            ),
            None,
        )
        if created is None:
            continue
        remaining.remove(deleted)
        remaining.remove(created)
        replacements.append(
            CodexFileDelta(
                path=created.path,
                operation=CodexChangeOperation.RENAME,
                before=deleted.before,
                after=created.after,
                unsafe_symlink=deleted.unsafe_symlink or created.unsafe_symlink,
            )
        )
    return sorted([*remaining, *replacements], key=lambda item: item.path)


def _identity_changed(before: CodexFileIdentity, after: CodexFileIdentity) -> bool:
    return (
        before.exists != after.exists
        or before.sha256 != after.sha256
        or before.size_bytes != after.size_bytes
        or before.is_binary != after.is_binary
        or before.is_symlink != after.is_symlink
    )


def _validate_delta(
    delta: CodexFileDelta, policy: CodexWritePolicy, authorized_paths: set[str]
) -> CodexWriteFailureKind | None:
    if delta.path not in authorized_paths:
        return CodexWriteFailureKind.UNAUTHORIZED_CHANGE
    if _protected(delta.path, policy):
        return CodexWriteFailureKind.PROTECTED_PATH
    if delta.unsafe_symlink or (delta.after is not None and delta.after.is_symlink):
        return CodexWriteFailureKind.SYMLINK_NOT_ALLOWED
    if delta.operation == CodexChangeOperation.DELETE:
        return CodexWriteFailureKind.DELETE_NOT_ALLOWED
    if delta.operation == CodexChangeOperation.RENAME:
        return CodexWriteFailureKind.RENAME_NOT_ALLOWED
    if delta.operation not in {CodexChangeOperation.MODIFY, CodexChangeOperation.CREATE}:
        return CodexWriteFailureKind.UNSUPPORTED_CHANGE
    if delta.operation == CodexChangeOperation.MODIFY and not policy.allow_modify:
        return CodexWriteFailureKind.UNSUPPORTED_CHANGE
    if delta.operation == CodexChangeOperation.CREATE and not policy.allow_create:
        return CodexWriteFailureKind.UNSUPPORTED_CHANGE
    if delta.after is None:
        return CodexWriteFailureKind.UNSUPPORTED_CHANGE
    if delta.after.size_bytes > policy.max_file_bytes:
        return CodexWriteFailureKind.FILE_TOO_LARGE
    if delta.after.is_binary and not policy.allow_binary:
        return CodexWriteFailureKind.BINARY_NOT_ALLOWED
    return None


def _operation_counts(deltas: Iterable[CodexFileDelta]) -> dict[CodexChangeOperation, int]:
    counts: dict[CodexChangeOperation, int] = {}
    for delta in deltas:
        counts[delta.operation] = counts.get(delta.operation, 0) + 1
    return counts


def _authorized_paths(paths: Iterable[str]) -> set[str] | None:
    try:
        return {normalize_repository_relative_path(path) for path in paths}
    except ValueError:
        return None


def _protected(path: str, policy: CodexWritePolicy) -> bool:
    parts = path.split("/")
    return parts[0] in policy.protected_prefixes or any(
        part == ".env" or part.startswith(".env.") for part in parts
    )


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_binary(path: Path) -> bool:
    with path.open("rb") as source:
        return b"\0" in source.read(_BINARY_SAMPLE_SIZE)


def _ok(result: GitCommandResult) -> bool:
    return not result.unavailable and not result.timed_out and result.exit_code == 0


def _reject(
    failure_kind: CodexWriteFailureKind,
    message: str,
    rejected_paths: Iterable[str] = (),
    delta: CodexWorkspaceDelta | None = None,
    source_revalidated: bool = False,
    workspace_integrity_valid: bool = False,
) -> CodexWorkspaceDeltaValidationResult:
    return CodexWorkspaceDeltaValidationResult(
        detected=delta is not None,
        valid=False,
        delta=delta,
        failure_kind=failure_kind,
        rejected_paths=sorted(rejected_paths),
        source_revalidated=source_revalidated,
        workspace_integrity_valid=workspace_integrity_valid,
        message=message,
    )
