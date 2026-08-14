"""Read-only CAR authority for validating untrusted coding proposal patches."""

from pathlib import Path

from car.authorization import AuthorizedPathKind, classify_authorized_path
from car.coding.models import (
    CodingExecutionPolicy,
    CodingProposal,
    CodingTaskContext,
    FileChangeOperation,
    normalize_repository_relative_path,
)
from car.patching.models import (
    ParsedFilePatch,
    ParsedPatchOperation,
    PatchValidationPolicy,
    PatchValidationResult,
    PatchViolation,
    PatchViolationKind,
)
from car.patching.parser import PatchParseError, parse_file_patch


class PatchValidator:
    """Validate only; this class never writes files or invokes external tools."""

    def __init__(self, policy: PatchValidationPolicy | None = None) -> None:
        self.policy = policy or PatchValidationPolicy()

    def validate(
        self,
        proposal: CodingProposal,
        context: CodingTaskContext,
        repository_root: Path,
        execution_policy: CodingExecutionPolicy | None = None,
    ) -> PatchValidationResult:
        """Return machine-readable validation evidence without modifying the workspace."""
        execution_policy = execution_policy or CodingExecutionPolicy()
        root = repository_root.resolve()
        maximum_files = min(self.policy.max_files, execution_policy.max_files_per_proposal)
        if len(proposal.changes) > maximum_files:
            return self._reject(
                PatchViolationKind.TOO_MANY_FILES, None, "file count exceeds policy"
            )
        total_bytes = sum(len(change.patch.encode("utf-8")) for change in proposal.changes)
        if total_bytes > self.policy.max_total_patch_bytes:
            return self._reject(
                PatchViolationKind.PATCH_TOO_LARGE, None, "total patch size exceeds policy"
            )

        selected = set(context.task_authorized_paths)
        parsed_files: list[ParsedFilePatch] = []
        task_changed_paths: list[str] = []
        auxiliary_changed_paths: list[str] = []
        seen_paths: set[str] = set()
        for change in proposal.changes:
            patch_size = len(change.patch.encode("utf-8"))
            if patch_size > self.policy.max_patch_bytes_per_file:
                return self._reject(
                    PatchViolationKind.PATCH_TOO_LARGE, change.path, "patch size exceeds policy"
                )
            try:
                parsed = parse_file_patch(change.patch)
            except PatchParseError as error:
                return self._reject(error.kind, change.path, str(error))
            result = self._validate_change(
                change.operation,
                change.path,
                parsed,
                selected,
                root,
                execution_policy,
            )
            if result is not None:
                return result
            if parsed.path in seen_paths:
                return self._reject(
                    PatchViolationKind.MULTIPLE_FILES_IN_CHANGE,
                    parsed.path,
                    "duplicate target path",
                )
            seen_paths.add(parsed.path)
            if len(parsed.hunks) > self.policy.max_hunks_per_file:
                return self._reject(
                    PatchViolationKind.PATCH_TOO_LARGE, parsed.path, "too many hunks"
                )
            parsed_files.append(parsed)
            authorization = self._authorization(parsed.path, selected)
            if authorization == AuthorizedPathKind.TASK or authorization is None:
                task_changed_paths.append(parsed.path)
            else:
                auxiliary_changed_paths.append(parsed.path)
        return PatchValidationResult.accepted(
            parsed_files,
            task_changed_paths=task_changed_paths,
            auxiliary_changed_paths=auxiliary_changed_paths,
        )

    def _validate_change(
        self,
        declared_operation: FileChangeOperation,
        declared_path: str,
        parsed: ParsedFilePatch,
        selected: set[str],
        root: Path,
        execution_policy: CodingExecutionPolicy,
    ) -> PatchValidationResult | None:
        if parsed.operation == ParsedPatchOperation.DELETE:
            return self._reject(
                PatchViolationKind.DELETE_NOT_SUPPORTED, declared_path, "delete diffs"
            )
        if parsed.operation == ParsedPatchOperation.RENAME:
            return self._reject(
                PatchViolationKind.RENAME_NOT_SUPPORTED, declared_path, "rename diffs"
            )
        if parsed.operation.value != declared_operation.value:
            return self._reject(
                PatchViolationKind.OPERATION_MISMATCH,
                declared_path,
                "declared operation differs from diff",
            )
        try:
            old_path = self._normalize_diff_path(parsed.old_path, allow_null=True)
            new_path = self._normalize_diff_path(parsed.new_path, allow_null=True)
            normalized_declared = normalize_repository_relative_path(declared_path)
        except ValueError:
            return self._reject(
                PatchViolationKind.PATH_ESCAPE, declared_path, "unsafe repository path"
            )
        target_path = new_path if new_path != "/dev/null" else old_path
        if normalized_declared != target_path:
            return self._reject(
                PatchViolationKind.PATH_MISMATCH, declared_path, "diff path differs from proposal"
            )
        if self._is_protected(target_path):
            return self._reject(PatchViolationKind.PROTECTED_PATH, target_path, "protected path")
        target = root / target_path
        if declared_operation == FileChangeOperation.MODIFY and target.is_symlink():
            return self._reject(
                PatchViolationKind.SYMLINK_NOT_ALLOWED, target_path, "symlink target"
            )
        if not self._is_within_root(root, target):
            return self._reject(
                PatchViolationKind.PATH_ESCAPE, target_path, "path leaves repository root"
            )
        if declared_operation == FileChangeOperation.MODIFY:
            if not execution_policy.allow_modify_files:
                return self._reject(
                    PatchViolationKind.UNAUTHORIZED_FILE, target_path, "modify disabled"
                )
            if self._authorization(target_path, selected) is None:
                return self._reject(
                    PatchViolationKind.UNAUTHORIZED_FILE, target_path, "file was not selected"
                )
            if not target.exists():
                return self._reject(
                    PatchViolationKind.TARGET_NOT_FOUND, target_path, "target does not exist"
                )
            if not target.is_file():
                return self._reject(
                    PatchViolationKind.TARGET_NOT_REGULAR_FILE,
                    target_path,
                    "target is not a regular file",
                )
        else:
            if not execution_policy.allow_create_files:
                return self._reject(
                    PatchViolationKind.UNAUTHORIZED_FILE, target_path, "create disabled"
                )
            if target.exists() or target.is_symlink():
                return self._reject(
                    PatchViolationKind.TARGET_ALREADY_EXISTS,
                    target_path,
                    "create target already exists",
                )
        return None

    @staticmethod
    def _normalize_diff_path(path: str, *, allow_null: bool) -> str:
        if allow_null and path == "/dev/null":
            return path
        return normalize_repository_relative_path(path)

    def _is_protected(self, path: str) -> bool:
        components = path.split("/")
        first = components[0]
        if first in self.policy.protected_prefixes:
            return True
        return any(component == ".env" or component.startswith(".env.") for component in components)

    def _authorization(self, path: str, selected: set[str]) -> AuthorizedPathKind | None:
        return classify_authorized_path(
            path,
            selected,
            safe_auxiliary_paths=self.policy.safe_auxiliary_paths,
        )

    @staticmethod
    def _is_within_root(root: Path, target: Path) -> bool:
        try:
            target.resolve(strict=False).relative_to(root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _reject(kind: PatchViolationKind, path: str | None, summary: str) -> PatchValidationResult:
        return PatchValidationResult.rejected(PatchViolation(kind=kind, path=path, summary=summary))
