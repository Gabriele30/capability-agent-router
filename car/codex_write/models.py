"""No-execution contracts for CAR-controlled acceptance of future Codex changes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from car.coding.models import normalize_repository_relative_path
from car.patching.models import PatchValidationPolicy


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexChangeOperation(StrEnum):
    MODIFY = "modify"
    CREATE = "create"
    DELETE = "delete"
    RENAME = "rename"


class CodexWriteFailureKind(StrEnum):
    DISABLED = "disabled"
    NOT_AUTHORIZED = "not_authorized"
    INVALID_BASELINE = "invalid_baseline"
    DELTA_DETECTION_FAILED = "delta_detection_failed"
    NO_CHANGES = "no_changes"
    UNEXPECTED_PATH = "unexpected_path"
    PROTECTED_PATH = "protected_path"
    TOO_MANY_FILES = "too_many_files"
    UNSUPPORTED_OPERATION = "unsupported_operation"
    FILE_TOO_LARGE = "file_too_large"
    BINARY_NOT_ALLOWED = "binary_not_allowed"
    UNSAFE_SYMLINK = "unsafe_symlink"
    CONCURRENT_MODIFICATION = "concurrent_modification"
    VALIDATION_FAILED = "validation_failed"
    VERIFICATION_FAILED = "verification_failed"
    ROLLBACK_FAILED = "rollback_failed"
    WORKSPACE_UNCERTAIN = "workspace_uncertain"
    INVALID_REPOSITORY = "invalid_repository"
    GIT_UNAVAILABLE = "git_unavailable"
    GIT_TIMEOUT = "git_timeout"
    WORKSPACE_SETUP_FAILED = "workspace_setup_failed"
    WORKSPACE_CLEANUP_FAILED = "workspace_cleanup_failed"


class CodexWriteAuthorization(_StrictModel):
    """Future caller-owned consent; false unless explicitly granted at runtime."""

    authorized: bool = False


class CodexWritePolicy(_StrictModel):
    """Future acceptance policy. It is intentionally disabled by default."""

    enabled: bool = False
    max_files: int = Field(default=10, ge=1, le=100)
    max_file_bytes: int = Field(default=64 * 1024, ge=1, le=1024 * 1024)
    allow_create: bool = True
    allow_modify: bool = True
    allow_delete: bool = False
    allow_rename: bool = False
    allow_binary: bool = False
    protected_prefixes: tuple[str, ...] = Field(
        default_factory=lambda: PatchValidationPolicy().protected_prefixes
    )


class CodexFileIdentity(_StrictModel):
    """Bounded identity metadata; file contents and absolute paths are excluded."""

    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    tracked: bool = False
    user_dirty: bool = False
    is_binary: bool = False
    is_symlink: bool = False

    @field_validator("path")
    @classmethod
    def repository_relative_path(cls, value: str) -> str:
        return normalize_repository_relative_path(value)


class CodexWorkspaceBaseline(_StrictModel):
    """Logical pre-Codex state captured by a future isolated-workspace service."""

    repository_name: str = Field(min_length=1)
    files: list[CodexFileIdentity] = Field(default_factory=list)
    repository_dirty: bool = False
    staged_paths: list[str] = Field(default_factory=list)
    untracked_paths: list[str] = Field(default_factory=list)

    @field_validator("staged_paths", "untracked_paths")
    @classmethod
    def paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]


class CodexFileDelta(_StrictModel):
    """A future exact delta observed from an isolated workspace, never provider text."""

    path: str
    operation: CodexChangeOperation
    before: CodexFileIdentity | None = None
    after: CodexFileIdentity | None = None
    unsafe_symlink: bool = False

    @field_validator("path")
    @classmethod
    def repository_relative_path(cls, value: str) -> str:
        return normalize_repository_relative_path(value)


class CodexChangeSet(_StrictModel):
    baseline: CodexWorkspaceBaseline
    deltas: list[CodexFileDelta] = Field(default_factory=list)


class CodexChangeValidationResult(_StrictModel):
    accepted: bool
    failure_kind: CodexWriteFailureKind | None = None
    path: str | None = None
    message: str


def validate_change_set(
    change_set: CodexChangeSet,
    policy: CodexWritePolicy,
    authorization: CodexWriteAuthorization,
) -> CodexChangeValidationResult:
    """Pure future-policy validation; it never invokes Codex or writes a workspace."""
    if not policy.enabled:
        return _reject(CodexWriteFailureKind.DISABLED, "controlled Codex writing is disabled")
    if not authorization.authorized:
        return _reject(CodexWriteFailureKind.NOT_AUTHORIZED, "explicit authorization is required")
    if not change_set.deltas:
        return _reject(CodexWriteFailureKind.NO_CHANGES, "no workspace changes were detected")
    if len(change_set.deltas) > policy.max_files:
        return _reject(CodexWriteFailureKind.TOO_MANY_FILES, "change count exceeds policy")
    for delta in change_set.deltas:
        if _is_protected(delta.path, policy):
            return _reject(CodexWriteFailureKind.PROTECTED_PATH, "protected path", delta.path)
        if delta.unsafe_symlink or (delta.after and delta.after.is_symlink):
            return _reject(CodexWriteFailureKind.UNSAFE_SYMLINK, "unsafe symlink", delta.path)
        if delta.operation == CodexChangeOperation.DELETE and not policy.allow_delete:
            return _reject(
                CodexWriteFailureKind.UNSUPPORTED_OPERATION, "delete is disabled", delta.path
            )
        if delta.operation == CodexChangeOperation.RENAME and not policy.allow_rename:
            return _reject(
                CodexWriteFailureKind.UNSUPPORTED_OPERATION, "rename is disabled", delta.path
            )
        if delta.operation == CodexChangeOperation.CREATE and not policy.allow_create:
            return _reject(
                CodexWriteFailureKind.UNSUPPORTED_OPERATION, "create is disabled", delta.path
            )
        if delta.operation == CodexChangeOperation.MODIFY and not policy.allow_modify:
            return _reject(
                CodexWriteFailureKind.UNSUPPORTED_OPERATION, "modify is disabled", delta.path
            )
        if delta.after and delta.after.size_bytes > policy.max_file_bytes:
            return _reject(
                CodexWriteFailureKind.FILE_TOO_LARGE, "file exceeds size limit", delta.path
            )
        if delta.after and delta.after.is_binary and not policy.allow_binary:
            return _reject(
                CodexWriteFailureKind.BINARY_NOT_ALLOWED, "binary files are disabled", delta.path
            )
    return CodexChangeValidationResult(accepted=True, message="change set accepted")


def baseline_matches(baseline: CodexWorkspaceBaseline, observed: CodexWorkspaceBaseline) -> bool:
    """Pure concurrent-change guard for a future apply boundary."""
    return baseline.model_dump() == observed.model_dump()


def _is_protected(path: str, policy: CodexWritePolicy) -> bool:
    parts = path.split("/")
    return parts[0] in policy.protected_prefixes or any(
        part == ".env" or part.startswith(".env.") for part in parts
    )


def _reject(
    kind: CodexWriteFailureKind, message: str, path: str | None = None
) -> CodexChangeValidationResult:
    return CodexChangeValidationResult(
        accepted=False, failure_kind=kind, path=path, message=message
    )
