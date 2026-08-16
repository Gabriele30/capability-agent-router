"""CAR-controlled, transactional application of already validated patch sets."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from car.coding.models import normalize_repository_relative_path
from car.patching.models import (
    ParsedFilePatch,
    ParsedHunk,
    PatchApplyFailureKind,
    PatchApplyResult,
    PatchTransactionState,
    PatchValidationPolicy,
    ValidatedPatchSet,
)
from car.rollback.snapshot import TargetSnapshot


class PatchApplyTransaction:
    """Runtime-only transaction retaining an in-memory snapshot for future verification."""

    def __init__(
        self,
        root: Path,
        snapshot: TargetSnapshot | None,
        write_bytes: Callable[[Path, bytes], None],
        result: PatchApplyResult,
    ) -> None:
        self.root = root
        self._snapshot = snapshot
        self._write_bytes = write_bytes
        self.result = result
        self.state = (
            PatchTransactionState.APPLIED if result.succeeded else PatchTransactionState.PREPARED
        )
        self._mutated: list[Path] = []
        self._created: list[Path] = []

    def record_modified(self, path: Path) -> None:
        self._mutated.append(path)

    def record_created(self, path: Path) -> None:
        self._mutated.append(path)
        self._created.append(path)

    def rollback(self) -> bool:
        """Restore only paths actually written by CAR; repeated rollback is a safe no-op."""
        if self.state == PatchTransactionState.ROLLED_BACK:
            return True
        if self._snapshot is None:
            return False
        try:
            for path in reversed(self._mutated):
                relative = path.relative_to(self.root)
                before = self._snapshot.files[relative]
                if before.existed:
                    self._write_bytes(path, before.content or b"")
                elif path.exists() or path.is_symlink():
                    path.unlink()
            self.state = PatchTransactionState.ROLLED_BACK
            self.result.rolled_back = True
            return True
        except (OSError, ValueError):
            self.result.rollback_failure_kind = PatchApplyFailureKind.ROLLBACK_FAILED
            self.state = PatchTransactionState.FAILED
            return False

    def finalize(self) -> None:
        """Discard the in-memory rollback handle after a future verification success."""
        if self.state == PatchTransactionState.APPLIED:
            self._snapshot = None
            self.state = PatchTransactionState.FINALIZED


class SafePatchApplier:
    """Apply only CAR-validated patches; no parser, provider, shell, or verifier belongs here."""

    def __init__(
        self,
        policy: PatchValidationPolicy | None = None,
        write_bytes: Callable[[Path, bytes], None] | None = None,
        snapshot_factory: Callable[[Path, list[Path]], TargetSnapshot] = TargetSnapshot.capture,
    ) -> None:
        self.policy = policy or PatchValidationPolicy()
        self._write_bytes = write_bytes or self._default_write_bytes
        self._snapshot_factory = snapshot_factory

    def apply(self, repository_root: Path, patch_set: ValidatedPatchSet) -> PatchApplyTransaction:
        """Snapshot all targets, then apply each strictly; failures rollback CAR-owned writes."""
        if not isinstance(patch_set, ValidatedPatchSet):
            raise TypeError("SafePatchApplier requires a ValidatedPatchSet")
        root = repository_root.resolve()
        targets = [root / file.path for file in patch_set.files]
        try:
            failure = self._preflight(root, patch_set.files)
            if failure is not None:
                return self._failed_transaction(root, None, failure)
            snapshot = self._snapshot_factory(root, targets)
        except OSError:
            return self._failed_transaction(
                root,
                None,
                (PatchApplyFailureKind.SNAPSHOT_FAILED, None, "unable to capture target snapshot"),
            )

        transaction = PatchApplyTransaction(
            root,
            snapshot,
            self._write_bytes,
            PatchApplyResult(attempted=True, succeeded=False, message="patch apply in progress"),
        )
        transaction.state = PatchTransactionState.PREPARED
        for file_patch in patch_set.files:
            target = root / file_patch.path
            try:
                content = self._prepare_target(root, target, file_patch)
                if file_patch.operation.value == "modify":
                    transaction.record_modified(target)
                    self._write_bytes(target, content)
                else:
                    transaction.record_created(target)
                    self._write_bytes(target, content)
            except _ApplyFailure as error:
                self._fail_and_rollback(transaction, error.kind, file_patch.path, error.summary)
                return transaction
            except OSError:
                kind = (
                    PatchApplyFailureKind.WRITE_FAILED
                    if file_patch.operation.value == "modify"
                    else PatchApplyFailureKind.CREATE_FAILED
                )
                self._fail_and_rollback(
                    transaction, kind, file_patch.path, "controlled file write failed"
                )
                return transaction
            except Exception:
                self._fail_and_rollback(
                    transaction,
                    PatchApplyFailureKind.UNEXPECTED_APPLY_ERROR,
                    file_patch.path,
                    "unexpected safe apply error",
                )
                return transaction

        transaction.result = PatchApplyResult(
            attempted=True,
            succeeded=True,
            changed_files=[file.path for file in patch_set.files],
            created_files=[
                file.path for file in patch_set.files if file.operation.value == "create"
            ],
            modified_files=[
                file.path for file in patch_set.files if file.operation.value == "modify"
            ],
            message="patch applied; verification not run",
        )
        transaction.state = PatchTransactionState.APPLIED
        return transaction

    def _preflight(
        self, root: Path, files: list[ParsedFilePatch]
    ) -> tuple[PatchApplyFailureKind, str | None, str] | None:
        if len(files) > self.policy.max_files:
            return (PatchApplyFailureKind.TARGET_CHANGED, None, "file count exceeds apply policy")
        if len({file.path for file in files}) != len(files):
            return (PatchApplyFailureKind.TARGET_CHANGED, None, "duplicate target path")
        for file_patch in files:
            target = root / file_patch.path
            failure = self._structural_check(root, target, file_patch)
            if failure is not None:
                return failure
        return None

    def _prepare_target(self, root: Path, target: Path, file_patch: ParsedFilePatch) -> bytes:
        failure = self._structural_check(root, target, file_patch)
        if failure is not None:
            raise _ApplyFailure(*failure)
        if file_patch.operation.value == "create":
            return self._apply_hunks([], file_patch.hunks, "\n", is_create=True).encode("utf-8")
        try:
            raw = target.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise _ApplyFailure(
                PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH,
                "target is not supported UTF-8 text",
            ) from error
        without_crlf = text.replace("\r\n", "")
        if "\r" in without_crlf or ("\r\n" in text and "\n" in without_crlf):
            raise _ApplyFailure(
                PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH,
                "mixed or unsupported newline style",
            )
        newline = "\r\n" if "\r\n" in text else "\n"
        source = text.splitlines()
        return self._apply_hunks(
            source,
            file_patch.hunks,
            newline,
            is_create=False,
            source_ends_with_newline=not raw or raw.endswith(b"\n"),
        ).encode("utf-8")

    def _structural_check(
        self, root: Path, target: Path, file_patch: ParsedFilePatch
    ) -> tuple[PatchApplyFailureKind, str | None, str] | None:
        if file_patch.operation.value not in {"modify", "create"}:
            return (
                PatchApplyFailureKind.UNEXPECTED_APPLY_ERROR,
                file_patch.path,
                "unsupported validated operation",
            )
        try:
            normalized = normalize_repository_relative_path(file_patch.path)
            if normalized != file_patch.path or self._is_protected(normalized):
                return (PatchApplyFailureKind.TARGET_CHANGED, file_patch.path, "unsafe target path")
            target.resolve(strict=False).relative_to(root)
        except ValueError:
            return (
                PatchApplyFailureKind.TARGET_CHANGED,
                file_patch.path,
                "target leaves repository",
            )
        if file_patch.operation.value == "modify":
            if not target.exists():
                return (
                    PatchApplyFailureKind.TARGET_NOT_FOUND,
                    file_patch.path,
                    "target no longer exists",
                )
            if target.is_symlink():
                return (
                    PatchApplyFailureKind.SYMLINK_NOT_ALLOWED,
                    file_patch.path,
                    "symlink target",
                )
            if not target.is_file():
                return (
                    PatchApplyFailureKind.TARGET_NOT_REGULAR_FILE,
                    file_patch.path,
                    "target is not a regular file",
                )
        else:
            if target.exists() or target.is_symlink():
                return (
                    PatchApplyFailureKind.TARGET_ALREADY_EXISTS,
                    file_patch.path,
                    "create target already exists",
                )
            parent = target.parent
            if not parent.exists() or not parent.is_dir():
                return (
                    PatchApplyFailureKind.TARGET_NOT_FOUND,
                    file_patch.path,
                    "create parent directory is unavailable",
                )
            try:
                parent.resolve().relative_to(root)
            except ValueError:
                return (PatchApplyFailureKind.SYMLINK_NOT_ALLOWED, file_patch.path, "unsafe parent")
        return None

    def _apply_hunks(
        self,
        source: list[str],
        hunks: list[ParsedHunk],
        newline: str,
        *,
        is_create: bool,
        source_ends_with_newline: bool = True,
    ) -> str:
        output: list[str] = []
        cursor = 0
        result_ends_with_newline = source_ends_with_newline
        for hunk in hunks:
            start = hunk.old_start if is_create else max(hunk.old_start - 1, 0)
            if start < cursor or start < 0 or start > len(source):
                raise _ApplyFailure(PatchApplyFailureKind.HUNK_RANGE_INVALID, "invalid hunk range")
            if is_create and (hunk.old_start != 0 or hunk.old_count != 0):
                raise _ApplyFailure(PatchApplyFailureKind.HUNK_RANGE_INVALID, "invalid create hunk")
            expected = [line.content for line in hunk.lines if line.prefix in {" ", "-"}]
            if source[start : start + hunk.old_count] != expected:
                raise _ApplyFailure(
                    PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH, "hunk context differs from target"
                )
            touches_source_eof = start + hunk.old_count == len(source)
            if not hunk.old_ends_with_newline and (
                not touches_source_eof or source_ends_with_newline
            ):
                raise _ApplyFailure(
                    PatchApplyFailureKind.HUNK_CONTEXT_MISMATCH,
                    "newline marker differs from target",
                )
            if not hunk.new_ends_with_newline:
                if not touches_source_eof:
                    raise _ApplyFailure(
                        PatchApplyFailureKind.HUNK_RANGE_INVALID,
                        "newline marker is not at target end",
                    )
                result_ends_with_newline = False
            elif not hunk.old_ends_with_newline:
                result_ends_with_newline = True
            output.extend(source[cursor:start])
            output.extend(line.content for line in hunk.lines if line.prefix in {" ", "+"})
            cursor = start + hunk.old_count
        output.extend(source[cursor:])
        if not output:
            return ""
        return newline.join(output) + (newline if result_ends_with_newline else "")

    def _is_protected(self, path: str) -> bool:
        components = path.split("/")
        if components[0] in self.policy.protected_prefixes:
            return True
        return any(component == ".env" or component.startswith(".env.") for component in components)

    @staticmethod
    def _default_write_bytes(path: Path, content: bytes) -> None:
        path.write_bytes(content)

    def _failed_transaction(
        self,
        root: Path,
        snapshot: TargetSnapshot | None,
        failure: tuple[PatchApplyFailureKind, str | None, str],
    ) -> PatchApplyTransaction:
        kind, path, summary = failure
        transaction = PatchApplyTransaction(
            root,
            snapshot,
            self._write_bytes,
            PatchApplyResult(
                attempted=False,
                succeeded=False,
                failure_kind=kind,
                failure_path=path,
                message=summary,
            ),
        )
        transaction.state = PatchTransactionState.FAILED
        return transaction

    @staticmethod
    def _fail_and_rollback(
        transaction: PatchApplyTransaction,
        kind: PatchApplyFailureKind,
        path: str,
        summary: str,
    ) -> None:
        transaction.result.failure_kind = kind
        transaction.result.failure_path = path
        transaction.result.message = summary
        transaction.result.succeeded = False
        if not transaction.rollback():
            transaction.state = PatchTransactionState.FAILED


class _ApplyFailure(Exception):
    def __init__(self, kind: PatchApplyFailureKind, summary: str) -> None:
        self.kind = kind
        self.summary = summary
        super().__init__(summary)
