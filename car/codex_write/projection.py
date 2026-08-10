"""Safe, read-source-only projection of a B1 baseline into isolated Git state."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from car.coding.models import normalize_repository_relative_path

from .baseline import SourceBaseline, SourceBaselineService
from .models import CodexFileIdentity, CodexWriteFailureKind, CodexWritePolicy
from .workspace import IsolatedCodexWorkspace, IsolatedWorkspaceManager


@dataclass(frozen=True)
class ProjectedIsolatedWorkspace:
    """Runtime-only workspace identity; source and temporary paths are not serialized."""

    workspace: IsolatedCodexWorkspace = field(repr=False)
    baseline_digest: str = ""
    baseline_head_oid: str = ""
    modified_paths: tuple[str, ...] = ()
    untracked_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()

    def metadata(self) -> dict[str, object]:
        """Return only bounded, repository-relative metadata for future handoffs."""
        return {
            "baseline_digest": self.baseline_digest,
            "baseline_head_oid": self.baseline_head_oid,
            "modified_paths": self.modified_paths,
            "untracked_paths": self.untracked_paths,
            "deleted_paths": self.deleted_paths,
        }


@dataclass(frozen=True)
class ProjectionResult:
    attempted: bool
    succeeded: bool
    baseline_revalidated_before: bool = False
    workspace_created: bool = False
    projected_workspace: ProjectedIsolatedWorkspace | None = field(default=None, repr=False)
    projected_modified_paths: tuple[str, ...] = ()
    projected_untracked_paths: tuple[str, ...] = ()
    projected_deleted_paths: tuple[str, ...] = ()
    post_revalidated: bool = False
    failure_kind: CodexWriteFailureKind | None = None
    failure_paths: tuple[str, ...] = ()
    message: str = ""

    def metadata(self) -> dict[str, object]:
        """Return a content-free result representation without runtime paths."""
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "baseline_revalidated_before": self.baseline_revalidated_before,
            "workspace_created": self.workspace_created,
            "projected_modified_paths": self.projected_modified_paths,
            "projected_untracked_paths": self.projected_untracked_paths,
            "projected_deleted_paths": self.projected_deleted_paths,
            "post_revalidated": self.post_revalidated,
            "failure_kind": self.failure_kind,
            "failure_paths": self.failure_paths,
            "message": self.message,
            "projected_workspace": (
                self.projected_workspace.metadata()
                if self.projected_workspace is not None
                else None
            ),
        }


class BaselineProjectionService:
    """Compose B1 observations with 5E2-A worktrees, without provider execution."""

    def __init__(
        self,
        baseline_service: SourceBaselineService | None = None,
        workspace_manager: IsolatedWorkspaceManager | None = None,
        post_projection_hook: Callable[[], None] | None = None,
    ) -> None:
        self._baseline_service = baseline_service or SourceBaselineService()
        self._workspace_manager = workspace_manager or IsolatedWorkspaceManager()
        self._post_projection_hook = post_projection_hook

    def project(
        self,
        source_repository: Path,
        baseline: SourceBaseline,
        policy: CodexWritePolicy,
        authorized_untracked_paths: Iterable[str] = (),
    ) -> ProjectionResult:
        """Create a disposable HEAD workspace and overlay only authorized baseline state."""
        requested = _authorized_paths(authorized_untracked_paths)
        if requested is None:
            return _failure(CodexWriteFailureKind.UNEXPECTED_PATH, "invalid untracked path")
        known_untracked = {identity.path for identity in baseline.files if identity.untracked}
        unexpected = requested - known_untracked
        if unexpected:
            return _failure(
                CodexWriteFailureKind.UNEXPECTED_PATH,
                "untracked path absent from baseline",
                unexpected,
            )
        before = self._baseline_service.revalidate(source_repository, baseline, policy)
        if not before.matches:
            return _failure(
                CodexWriteFailureKind.CONCURRENT_MODIFICATION,
                "source baseline changed before projection",
                before.changed_paths,
            )
        created = self._workspace_manager.create(source_repository, revision=baseline.head_oid)
        if not created.created or created.workspace is None:
            return ProjectionResult(
                attempted=True,
                succeeded=False,
                baseline_revalidated_before=True,
                failure_kind=created.failure_kind,
                message=created.message,
            )
        workspace = created.workspace
        result = self._overlay(source_repository, baseline, workspace, policy, requested)
        if result.failure_kind is None and self._post_projection_hook is not None:
            self._post_projection_hook()
        if result.failure_kind is None:
            post = self._baseline_service.revalidate(source_repository, baseline, policy)
            if not post.matches:
                result = _failure(
                    CodexWriteFailureKind.CONCURRENT_MODIFICATION,
                    "source baseline changed during projection",
                    post.changed_paths,
                )
            else:
                result = ProjectionResult(
                    attempted=True,
                    succeeded=True,
                    baseline_revalidated_before=True,
                    workspace_created=True,
                    projected_workspace=ProjectedIsolatedWorkspace(
                        workspace=workspace,
                        baseline_digest=baseline.baseline_digest,
                        baseline_head_oid=baseline.head_oid,
                        modified_paths=result.projected_modified_paths,
                        untracked_paths=result.projected_untracked_paths,
                        deleted_paths=result.projected_deleted_paths,
                    ),
                    projected_modified_paths=result.projected_modified_paths,
                    projected_untracked_paths=result.projected_untracked_paths,
                    projected_deleted_paths=result.projected_deleted_paths,
                    post_revalidated=True,
                )
        if result.succeeded:
            return result
        cleanup = self._workspace_manager.cleanup(workspace)
        if not cleanup.removed:
            return ProjectionResult(
                attempted=True,
                succeeded=False,
                baseline_revalidated_before=True,
                workspace_created=True,
                failure_kind=cleanup.failure_kind,
                failure_paths=result.failure_paths,
                message=cleanup.message,
            )
        return ProjectionResult(
            attempted=True,
            succeeded=False,
            baseline_revalidated_before=True,
            workspace_created=True,
            projected_modified_paths=result.projected_modified_paths,
            projected_untracked_paths=result.projected_untracked_paths,
            projected_deleted_paths=result.projected_deleted_paths,
            failure_kind=result.failure_kind,
            failure_paths=result.failure_paths,
            message=result.message,
        )

    def cleanup(self, projected: ProjectedIsolatedWorkspace):
        """Dispose a successful projected workspace through its owning lifecycle manager."""
        return self._workspace_manager.cleanup(projected.workspace)

    def _overlay(
        self,
        source: Path,
        baseline: SourceBaseline,
        workspace: IsolatedCodexWorkspace,
        policy: CodexWritePolicy,
        authorized_untracked: set[str],
    ) -> ProjectionResult:
        candidates = [
            identity
            for identity in baseline.files
            if identity.untracked
            and identity.path in authorized_untracked
            or identity.tracked
            and (not identity.exists or identity.staged or identity.unstaged)
        ]
        if len(candidates) > policy.max_projection_files:
            return _failure(CodexWriteFailureKind.TOO_MANY_FILES, "projection file limit exceeded")
        modified: list[str] = []
        untracked: list[str] = []
        deleted: list[str] = []
        copied_bytes = 0
        for identity in candidates:
            if identity.protected and (identity.staged or identity.unstaged or identity.untracked):
                return _failure(
                    CodexWriteFailureKind.PROTECTED_PATH,
                    "protected path cannot project",
                    [identity.path],
                )
            needs_copy = identity.untracked or (
                identity.tracked and identity.exists and (identity.staged or identity.unstaged)
            )
            needs_delete = identity.tracked and not identity.exists
            if not needs_copy and not needs_delete:
                continue
            if identity.is_symlink:
                return _failure(
                    CodexWriteFailureKind.UNSAFE_SYMLINK,
                    "symlink projection is unsupported",
                    [identity.path],
                )
            if needs_copy:
                if identity.size_bytes > policy.max_file_bytes:
                    return _failure(
                        CodexWriteFailureKind.FILE_TOO_LARGE,
                        "file exceeds projection limit",
                        [identity.path],
                    )
                copied_bytes += identity.size_bytes
                if copied_bytes > policy.max_projection_total_bytes:
                    return _failure(
                        CodexWriteFailureKind.TOTAL_SIZE_EXCEEDED, "projection byte limit exceeded"
                    )
                failure = _copy_and_verify(source, workspace.path, identity)
                if failure is not None:
                    return _failure(
                        failure, "projection copy or validation failed", [identity.path]
                    )
                (untracked if identity.untracked else modified).append(identity.path)
            if needs_delete:
                failure = _delete_and_verify(workspace.path, identity)
                if failure is not None:
                    return _failure(
                        failure, "projection deletion validation failed", [identity.path]
                    )
                deleted.append(identity.path)
        return ProjectionResult(
            attempted=True,
            succeeded=False,
            projected_modified_paths=tuple(modified),
            projected_untracked_paths=tuple(untracked),
            projected_deleted_paths=tuple(deleted),
        )


def _copy_and_verify(
    source_root: Path, workspace_root: Path, identity: CodexFileIdentity
) -> CodexWriteFailureKind | None:
    source = _safe_path(source_root, identity.path)
    destination = _safe_path(workspace_root, identity.path)
    if source is None or destination is None or _has_symlink_ancestor(workspace_root, destination):
        return CodexWriteFailureKind.UNEXPECTED_PATH
    try:
        source_info = source.lstat()
    except OSError:
        return CodexWriteFailureKind.CONCURRENT_MODIFICATION
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_size != identity.size_bytes:
        return CodexWriteFailureKind.CONCURRENT_MODIFICATION
    if destination.exists() and destination.is_symlink():
        return CodexWriteFailureKind.UNSAFE_SYMLINK
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open("rb") as input_file, destination.open("wb") as output_file:
            for chunk in iter(lambda: input_file.read(64 * 1024), b""):
                digest.update(chunk)
                output_file.write(chunk)
    except OSError:
        return CodexWriteFailureKind.PROJECTION_FAILED
    if digest.hexdigest() != identity.sha256:
        return CodexWriteFailureKind.CONCURRENT_MODIFICATION
    return _verify_destination(destination, identity)


def _delete_and_verify(
    workspace_root: Path, identity: CodexFileIdentity
) -> CodexWriteFailureKind | None:
    destination = _safe_path(workspace_root, identity.path)
    if destination is None or _has_symlink_ancestor(workspace_root, destination):
        return CodexWriteFailureKind.UNEXPECTED_PATH
    try:
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            return CodexWriteFailureKind.UNSAFE_SYMLINK
        if destination.exists():
            destination.unlink()
    except OSError:
        return CodexWriteFailureKind.PROJECTION_FAILED
    return None if not destination.exists() else CodexWriteFailureKind.PROJECTION_VALIDATION_FAILED


def _verify_destination(
    destination: Path, identity: CodexFileIdentity
) -> CodexWriteFailureKind | None:
    try:
        info = destination.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != identity.size_bytes:
            return CodexWriteFailureKind.PROJECTION_VALIDATION_FAILED
        digest = hashlib.sha256()
        with destination.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return CodexWriteFailureKind.PROJECTION_VALIDATION_FAILED
    if digest.hexdigest() != identity.sha256:
        return CodexWriteFailureKind.PROJECTION_VALIDATION_FAILED
    return None


def _safe_path(root: Path, relative_path: str) -> Path | None:
    try:
        normalized = normalize_repository_relative_path(relative_path)
    except ValueError:
        return None
    candidate = root.joinpath(*normalized.split("/"))
    try:
        candidate.resolve(strict=False).relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _has_symlink_ancestor(root: Path, path: Path) -> bool:
    current = path.parent
    while current != root:
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
        current = current.parent
    return False


def _authorized_paths(paths: Iterable[str]) -> set[str] | None:
    try:
        return {normalize_repository_relative_path(path) for path in paths}
    except ValueError:
        return None


def _failure(
    kind: CodexWriteFailureKind, message: str, paths: Iterable[str] = ()
) -> ProjectionResult:
    return ProjectionResult(
        attempted=True,
        succeeded=False,
        failure_kind=kind,
        failure_paths=tuple(sorted(paths)),
        message=message,
    )
