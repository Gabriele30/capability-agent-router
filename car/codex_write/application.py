"""Transactional, non-finalizing application of already validated isolated Codex bytes."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from pathlib import Path

from car.rollback.snapshot import TargetSnapshot

from .baseline import SourceBaseline, SourceBaselineService
from .delta import CodexWorkspaceDeltaDetector, CodexWorkspaceDeltaValidator
from .models import (
    CodexChangeOperation,
    CodexFileIdentity,
    CodexSourceApplicationResult,
    CodexSourceTransactionState,
    CodexWriteAuthorization,
    CodexWriteFailureKind,
    CodexWritePolicy,
    ValidatedCodexChangeSet,
)
from .projection import ProjectedIsolatedWorkspace

_CHUNK_SIZE = 64 * 1024


class AppliedCodexSourceTransaction:
    """In-memory target snapshot and reversible source state pending future verification."""

    def __init__(
        self,
        root: Path,
        snapshot: TargetSnapshot,
        written_hashes: dict[Path, str],
        source_baseline: SourceBaseline,
        branch: str,
        index_digest: str,
    ) -> None:
        self.root = root
        self._snapshot = snapshot
        self._written_hashes = written_hashes
        self._source_baseline = source_baseline
        self._branch = branch
        self._index_digest = index_digest
        self.state = CodexSourceTransactionState.APPLIED_PENDING_VERIFICATION

    def rollback(self) -> bool:
        if self.state == CodexSourceTransactionState.ROLLED_BACK:
            return True
        try:
            for relative, before in reversed(list(self._snapshot.files.items())):
                target = self.root / relative
                expected = self._written_hashes.get(relative)
                if expected is None:
                    continue
                if not target.exists() or target.is_symlink() or _digest(target) != expected:
                    self.state = CodexSourceTransactionState.FAILED
                    return False
                if before.existed:
                    _atomic_replace(target, before.content or b"")
                else:
                    target.unlink()
            self.state = CodexSourceTransactionState.ROLLED_BACK
            return True
        except OSError:
            self.state = CodexSourceTransactionState.FAILED
            return False

    def finalize(self) -> None:
        """Discard rollback capability after a future verifier decides acceptance."""
        if self.state == CodexSourceTransactionState.APPLIED_PENDING_VERIFICATION:
            self._snapshot = TargetSnapshot(root=self.root, files={})
            self.state = CodexSourceTransactionState.FINALIZED

    @property
    def changed_paths(self) -> list[str]:
        return [path.as_posix() for path in self._snapshot.files]

    def applied_identities_match(self) -> bool:
        """Ensure all targets still contain exactly the bytes written by B1."""
        try:
            for relative, expected in self._written_hashes.items():
                target = self.root / relative
                info = target.lstat()
                if not stat.S_ISREG(info.st_mode) or _digest(target) != expected:
                    return False
            return True
        except OSError:
            return False

    def source_integrity_matches(self, policy: CodexWritePolicy) -> bool:
        """Allow only the expected B1 target delta over the exact captured source state."""
        if (
            _git_branch(self.root) != self._branch
            or _git_index_digest(self.root) != self._index_digest
        ):
            return False
        captured = SourceBaselineService().capture(self.root, policy)
        if not captured.captured or captured.baseline is None:
            return False
        observed = {item.path: item for item in captured.baseline.files}
        expected = {item.path: item for item in self._source_baseline.files}
        targets = {path.as_posix() for path in self._snapshot.files}
        if set(observed) - targets != set(expected) - targets:
            return False
        if any(observed[path] != expected[path] for path in set(expected) - targets):
            return False
        for relative, snapshot in self._snapshot.files.items():
            path = relative.as_posix()
            identity = observed.get(path)
            if snapshot.existed:
                before = expected.get(path)
                if identity is None or before is None or not _same_git_metadata(identity, before):
                    return False
            elif identity is None or not identity.untracked:
                return False
        return self.applied_identities_match()


class CodexSourceApplicationService:
    """Apply freshly revalidated isolated bytes without verification or Git mutation."""

    def __init__(
        self,
        detector: CodexWorkspaceDeltaDetector,
        validator: CodexWorkspaceDeltaValidator | None = None,
        baseline_service: SourceBaselineService | None = None,
    ) -> None:
        self._detector = detector
        self._validator = validator or CodexWorkspaceDeltaValidator()
        self._baseline_service = baseline_service or SourceBaselineService()

    def apply(
        self,
        source_repository: Path,
        projected_workspace: ProjectedIsolatedWorkspace,
        validated_change_set: ValidatedCodexChangeSet,
        source_baseline: SourceBaseline,
        policy: CodexWritePolicy,
    ) -> tuple[CodexSourceApplicationResult, AppliedCodexSourceTransaction | None]:
        root = source_repository.resolve()
        if (
            root != projected_workspace.workspace.source_root.resolve()
            or projected_workspace.baseline_digest != source_baseline.baseline_digest
            or projected_workspace.baseline_head_oid != source_baseline.head_oid
            or validated_change_set.baseline_digest != source_baseline.baseline_digest
            or validated_change_set.change_set.baseline.baseline_digest
            != source_baseline.baseline_digest
            or validated_change_set.change_set.baseline.head_oid != source_baseline.head_oid
        ):
            return _failure(CodexWriteFailureKind.WORKSPACE_CHANGED_AFTER_VALIDATION), None
        source = self._baseline_service.revalidate(root, source_baseline, policy)
        if not source.matches:
            return _failure(CodexWriteFailureKind.CONCURRENT_MODIFICATION), None
        expected_paths = tuple(delta.path for delta in validated_change_set.change_set.deltas)
        redetected = self._detector.detect(projected_workspace, source_baseline, policy)
        revalidated = self._validator.validate(
            redetected,
            source_baseline,
            policy,
            CodexWriteAuthorization(authorized=True),
            expected_paths,
            root,
        )
        if not revalidated.valid or revalidated.validated_change_set != validated_change_set:
            return _failure(
                CodexWriteFailureKind.WORKSPACE_CHANGED_AFTER_VALIDATION,
                source_revalidated=True,
                workspace_revalidated=False,
            ), None
        source = self._baseline_service.revalidate(root, source_baseline, policy)
        if not source.matches:
            return _failure(
                CodexWriteFailureKind.CONCURRENT_MODIFICATION,
                source_revalidated=False,
                workspace_revalidated=True,
            ), None
        deltas = validated_change_set.change_set.deltas
        targets: list[Path] = []
        contents: dict[Path, bytes] = {}
        try:
            for delta in deltas:
                target = _safe_target(root, delta.path)
                isolated = _safe_target(projected_workspace.workspace.path, delta.path)
                if (
                    target is None
                    or isolated is None
                    or _is_protected(delta.path, policy)
                    or not _safe_parent(root, target)
                ):
                    return _failure(
                        CodexWriteFailureKind.SOURCE_TARGET_UNSAFE,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                if delta.operation not in {
                    CodexChangeOperation.MODIFY,
                    CodexChangeOperation.CREATE,
                }:
                    return _failure(
                        CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                if delta.after is None or delta.after.is_symlink or delta.after.is_binary:
                    return _failure(
                        CodexWriteFailureKind.CONTENT_IDENTITY_MISMATCH,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                content = _validated_isolated_bytes(
                    isolated, delta.after.sha256, delta.after.size_bytes, policy
                )
                if content is None:
                    return _failure(
                        CodexWriteFailureKind.CONTENT_IDENTITY_MISMATCH,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                if delta.operation == CodexChangeOperation.MODIFY:
                    if not target.exists() or target.is_symlink() or not target.is_file():
                        return _failure(
                            CodexWriteFailureKind.SOURCE_TARGET_UNSAFE,
                            source_revalidated=True,
                            workspace_revalidated=True,
                        ), None
                elif target.exists() or target.is_symlink():
                    return _failure(
                        CodexWriteFailureKind.CREATE_TARGET_EXISTS,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                elif not target.parent.exists() or not target.parent.is_dir():
                    return _failure(
                        CodexWriteFailureKind.CREATE_PARENT_NOT_FOUND,
                        source_revalidated=True,
                        workspace_revalidated=True,
                    ), None
                targets.append(target)
                contents[target] = content
            snapshot = TargetSnapshot.capture(root, targets)
        except OSError:
            return _failure(
                CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                source_revalidated=True,
                workspace_revalidated=True,
            ), None

        branch = _git_branch(root)
        index_digest = _git_index_digest(root)
        if branch is None or index_digest is None:
            return _failure(
                CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                source_revalidated=True,
                workspace_revalidated=True,
            ), None
        transaction = AppliedCodexSourceTransaction(
            root, snapshot, {}, source_baseline, branch, index_digest
        )
        changed: list[str] = []
        created: list[str] = []
        modified: list[str] = []
        for delta, target in zip(deltas, targets, strict=True):
            before = snapshot.files[target.relative_to(root)]
            try:
                if delta.operation == CodexChangeOperation.MODIFY:
                    if (
                        not before.existed
                        or target.is_symlink()
                        or target.read_bytes() != before.content
                    ):
                        raise _ConcurrentTargetError
                    _atomic_replace(target, contents[target])
                    modified.append(delta.path)
                else:
                    if before.existed or target.exists() or target.is_symlink():
                        raise _ConcurrentTargetError
                    _exclusive_create(target, contents[target])
                    created.append(delta.path)
                transaction._written_hashes[target.relative_to(root)] = _digest(target)
                changed.append(delta.path)
            except _ConcurrentTargetError:
                return _rollback_failure(
                    transaction,
                    CodexWriteFailureKind.CONCURRENT_MODIFICATION,
                    changed,
                    created,
                    modified,
                )
            except OSError:
                return _rollback_failure(
                    transaction,
                    CodexWriteFailureKind.SOURCE_APPLICATION_FAILED,
                    changed,
                    created,
                    modified,
                )
        return (
            CodexSourceApplicationResult(
                attempted=True,
                applied=True,
                changed_paths=changed,
                created_paths=created,
                modified_paths=modified,
                source_revalidated=True,
                workspace_revalidated=True,
                message="source transaction applied; verification not run",
            ),
            transaction,
        )


class _ConcurrentTargetError(Exception):
    pass


def _rollback_failure(transaction, kind, changed, created, modified):
    rolled_back = transaction.rollback()
    return (
        CodexSourceApplicationResult(
            attempted=True,
            applied=False,
            failure_kind=kind if rolled_back else CodexWriteFailureKind.ROLLBACK_FAILED,
            changed_paths=changed,
            created_paths=created,
            modified_paths=modified,
            rollback_attempted=True,
            rollback_succeeded=rolled_back,
            source_revalidated=True,
            workspace_revalidated=True,
            message="source transaction failed and rollback was attempted",
        ),
        transaction,
    )


def _failure(kind, *, source_revalidated=False, workspace_revalidated=False):
    return CodexSourceApplicationResult(
        attempted=False,
        applied=False,
        failure_kind=kind,
        source_revalidated=source_revalidated,
        workspace_revalidated=workspace_revalidated,
        message="source application was blocked",
    )


def _safe_target(root: Path, relative: str) -> Path | None:
    try:
        candidate = root.joinpath(*relative.split("/"))
        candidate.resolve(strict=False).relative_to(root.resolve())
        return candidate
    except ValueError:
        return None


def _safe_parent(root: Path, target: Path) -> bool:
    current = target.parent
    while current != root:
        if current.is_symlink():
            return False
        current = current.parent
    return True


def _is_protected(path: str, policy: CodexWritePolicy) -> bool:
    parts = path.split("/")
    return parts[0] in policy.protected_prefixes or any(
        part == ".env" or part.startswith(".env.") for part in parts
    )


def _validated_isolated_bytes(
    path: Path, expected_hash: str | None, expected_size: int, policy
) -> bytes | None:
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_size != expected_size
            or info.st_size > policy.max_file_bytes
        ):
            return None
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
                chunks.append(chunk)
        return b"".join(chunks) if digest.hexdigest() == expected_hash else None
    except OSError:
        return None


def _atomic_replace(target: Path, content: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temporary:
        temporary.write(content)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    try:
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _exclusive_create(target: Path, content: bytes) -> None:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_branch(root: Path) -> str | None:
    return _git_output(root, ["symbolic-ref", "--short", "-q", "HEAD"])


def _git_index_digest(root: Path) -> str | None:
    output = _git_output(root, ["ls-files", "-s", "-z"], raw=True)
    return hashlib.sha256(output).hexdigest() if output is not None else None


def _git_output(root: Path, args: list[str], *, raw: bool = False) -> str | bytes | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            shell=False,
            text=not raw,
            encoding=None if raw else "utf-8",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout if raw else completed.stdout.strip()


def _same_git_metadata(current: CodexFileIdentity, before: CodexFileIdentity) -> bool:
    return (
        current.path == before.path
        and current.tracked == before.tracked
        and current.staged == before.staged
        and current.is_symlink == before.is_symlink
        and current.protected == before.protected
    )
