"""Structured, provider-neutral data contracts for parsed and validated patches."""

from enum import StrEnum

from pydantic import BaseModel, Field


class ParsedPatchOperation(StrEnum):
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    RENAME = "rename"


class ParsedHunkLine(BaseModel):
    prefix: str = Field(pattern=r"^[ +\-]$")
    content: str


class ParsedHunk(BaseModel):
    old_start: int = Field(ge=0)
    old_count: int = Field(ge=0)
    new_start: int = Field(ge=0)
    new_count: int = Field(ge=0)
    lines: list[ParsedHunkLine] = Field(min_length=1)


class ParsedFilePatch(BaseModel):
    path: str
    operation: ParsedPatchOperation
    old_path: str
    new_path: str
    hunks: list[ParsedHunk] = Field(min_length=1)


class ParsedPatchSet(BaseModel):
    files: list[ParsedFilePatch] = Field(min_length=1)


class PatchViolationKind(StrEnum):
    INVALID_DIFF = "invalid_diff"
    PATH_MISMATCH = "path_mismatch"
    PATH_ESCAPE = "path_escape"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_ALREADY_EXISTS = "target_already_exists"
    TARGET_NOT_REGULAR_FILE = "target_not_regular_file"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    OPERATION_MISMATCH = "operation_mismatch"
    UNAUTHORIZED_FILE = "unauthorized_file"
    PROTECTED_PATH = "protected_path"
    DELETE_NOT_SUPPORTED = "delete_not_supported"
    RENAME_NOT_SUPPORTED = "rename_not_supported"
    BINARY_PATCH_NOT_SUPPORTED = "binary_patch_not_supported"
    MODE_CHANGE_NOT_SUPPORTED = "mode_change_not_supported"
    MULTIPLE_FILES_IN_CHANGE = "multiple_files_in_change"
    HUNK_INVALID = "hunk_invalid"
    HUNK_COUNT_MISMATCH = "hunk_count_mismatch"
    HUNK_OVERLAP = "hunk_overlap"
    TOO_MANY_FILES = "too_many_files"
    PATCH_TOO_LARGE = "patch_too_large"


class PatchApplyFailureKind(StrEnum):
    TARGET_CHANGED = "target_changed"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_ALREADY_EXISTS = "target_already_exists"
    TARGET_NOT_REGULAR_FILE = "target_not_regular_file"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    HUNK_CONTEXT_MISMATCH = "hunk_context_mismatch"
    HUNK_RANGE_INVALID = "hunk_range_invalid"
    WRITE_FAILED = "write_failed"
    CREATE_FAILED = "create_failed"
    SNAPSHOT_FAILED = "snapshot_failed"
    ROLLBACK_FAILED = "rollback_failed"
    UNEXPECTED_APPLY_ERROR = "unexpected_apply_error"


class PatchTransactionState(StrEnum):
    PREPARED = "prepared"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    FINALIZED = "finalized"


class PatchViolation(BaseModel):
    kind: PatchViolationKind
    path: str | None = None
    summary: str


class PatchValidationPolicy(BaseModel):
    """Conservative limits for the read-only safe-patch boundary."""

    max_files: int = Field(default=10, ge=1, le=100)
    max_patch_bytes_per_file: int = Field(default=64 * 1024, ge=1, le=1024 * 1024)
    max_total_patch_bytes: int = Field(default=256 * 1024, ge=1, le=4 * 1024 * 1024)
    max_hunks_per_file: int = Field(default=100, ge=1, le=10_000)
    protected_prefixes: tuple[str, ...] = (
        ".git",
        ".car-context",
        ".venv",
        "venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "htmlcov",
    )


class ValidatedPatchSet(BaseModel):
    files: list[ParsedFilePatch] = Field(min_length=1)


class PatchValidationResult(BaseModel):
    valid: bool
    patch_set: ValidatedPatchSet | None = None
    violations: list[PatchViolation] = Field(default_factory=list)

    @classmethod
    def accepted(cls, files: list[ParsedFilePatch]) -> "PatchValidationResult":
        return cls(valid=True, patch_set=ValidatedPatchSet(files=files))

    @classmethod
    def rejected(cls, violation: PatchViolation) -> "PatchValidationResult":
        return cls(valid=False, violations=[violation])


class PatchApplyResult(BaseModel):
    attempted: bool
    succeeded: bool
    changed_files: list[str] = Field(default_factory=list)
    created_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    rolled_back: bool = False
    failure_kind: PatchApplyFailureKind | None = None
    failure_path: str | None = None
    rollback_failure_kind: PatchApplyFailureKind | None = None
    message: str
