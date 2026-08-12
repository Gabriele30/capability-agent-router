"""No-execution contracts for CAR-controlled acceptance of future Codex changes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from car.authorization import DEFAULT_SAFE_AUXILIARY_PATHS
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
    UNAUTHORIZED_CHANGE = "unauthorized_change"
    UNSUPPORTED_CHANGE = "unsupported_change"
    DELETE_NOT_ALLOWED = "delete_not_allowed"
    RENAME_NOT_ALLOWED = "rename_not_allowed"
    SYMLINK_NOT_ALLOWED = "symlink_not_allowed"
    CHANGE_LIMIT_EXCEEDED = "change_limit_exceeded"
    WORKSPACE_INTEGRITY_FAILED = "workspace_integrity_failed"
    WORKSPACE_CHANGED_AFTER_VALIDATION = "workspace_changed_after_validation"
    CONTENT_IDENTITY_MISMATCH = "content_identity_mismatch"
    SOURCE_APPLICATION_FAILED = "source_application_failed"
    SOURCE_TARGET_UNSAFE = "source_target_unsafe"
    CREATE_TARGET_EXISTS = "create_target_exists"
    CREATE_PARENT_NOT_FOUND = "create_parent_not_found"
    VERIFICATION_REQUIRED = "verification_required"
    PRE_VERIFICATION_INTEGRITY_FAILED = "pre_verification_integrity_failed"
    POST_VERIFICATION_INTEGRITY_FAILED = "post_verification_integrity_failed"
    VERIFICATION_TIMEOUT = "verification_timeout"
    FINALIZATION_FAILED = "finalization_failed"
    MALFORMED_GIT_STATUS = "malformed_git_status"
    TOTAL_SIZE_EXCEEDED = "total_size_exceeded"
    UNSUPPORTED_REPOSITORY_STATE = "unsupported_repository_state"
    PROJECTION_FAILED = "projection_failed"
    PROJECTION_VALIDATION_FAILED = "projection_validation_failed"
    INVALID_WORKSPACE = "invalid_workspace"
    CODEX_CLI_NOT_FOUND = "codex_cli_not_found"
    CODEX_NOT_AUTHENTICATED = "codex_not_authenticated"
    CODEX_NOT_READY = "codex_not_ready"
    CODEX_TIMEOUT = "codex_timeout"
    CODEX_PROCESS_ERROR = "codex_process_error"
    CODEX_NONZERO_EXIT = "codex_nonzero_exit"
    CODEX_INVALID_OUTPUT = "codex_invalid_output"


class CodexWriteAuthorization(_StrictModel):
    """Future caller-owned consent; false unless explicitly granted at runtime."""

    authorized: bool = False


class CodexWritePolicy(_StrictModel):
    """Future acceptance policy. It is intentionally disabled by default."""

    enabled: bool = False
    max_files: int = Field(default=10, ge=1, le=100)
    max_file_bytes: int = Field(default=64 * 1024, ge=1, le=1024 * 1024)
    max_baseline_files: int = Field(default=500, ge=1, le=2_000)
    max_baseline_total_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=32 * 1024 * 1024)
    max_projection_files: int = Field(default=50, ge=1, le=500)
    max_projection_total_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=32 * 1024 * 1024)
    codex_login_timeout_seconds: float = Field(default=10, gt=0, le=60)
    codex_write_timeout_seconds: float = Field(default=120, gt=0, le=900)
    codex_max_stdout_chars: int = Field(default=8_000, ge=100, le=100_000)
    codex_max_stderr_chars: int = Field(default=4_000, ge=100, le=100_000)
    allow_create: bool = True
    allow_modify: bool = True
    allow_delete: bool = False
    allow_rename: bool = False
    allow_binary: bool = False
    protected_prefixes: tuple[str, ...] = Field(
        default_factory=lambda: PatchValidationPolicy().protected_prefixes
    )
    safe_auxiliary_paths: tuple[str, ...] = DEFAULT_SAFE_AUXILIARY_PATHS


class CodexFileIdentity(_StrictModel):
    """Bounded identity metadata; file contents and absolute paths are excluded."""

    path: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    tracked: bool = False
    user_dirty: bool = False
    is_binary: bool = False
    is_symlink: bool = False
    exists: bool = True
    staged: bool = False
    unstaged: bool = False
    untracked: bool = False
    protected: bool = False
    symlink_target_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

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
    head_oid: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    baseline_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    total_bytes: int = Field(default=0, ge=0)

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


class CodexWorkspaceDelta(_StrictModel):
    """Untrusted, content-free filesystem delta observed in a projected workspace."""

    baseline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_head_oid: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    deltas: list[CodexFileDelta] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    operation_counts: dict[CodexChangeOperation, int] = Field(default_factory=dict)

    @field_validator("changed_paths")
    @classmethod
    def paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]


class ValidatedCodexChangeSet(_StrictModel):
    """Eligible-for-future-application change set; it never authorizes source writes."""

    change_set: CodexChangeSet
    baseline_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revalidated: bool = True
    workspace_integrity_valid: bool = True


class CodexWorkspaceDeltaValidationResult(_StrictModel):
    """Structured result for detection and validation of isolated Codex filesystem changes."""

    detected: bool
    valid: bool
    delta: CodexWorkspaceDelta | None = None
    validated_change_set: ValidatedCodexChangeSet | None = None
    failure_kind: CodexWriteFailureKind | None = None
    rejected_paths: list[str] = Field(default_factory=list)
    task_changed_paths: list[str] = Field(default_factory=list)
    auxiliary_changed_paths: list[str] = Field(default_factory=list)
    source_revalidated: bool = False
    workspace_integrity_valid: bool = False
    message: str

    @field_validator("rejected_paths", "task_changed_paths", "auxiliary_changed_paths")
    @classmethod
    def rejected_paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]


class CodexSourceTransactionState(StrEnum):
    APPLIED_PENDING_VERIFICATION = "applied_pending_verification"
    ROLLED_BACK = "rolled_back"
    FINALIZED = "finalized"
    FAILED = "failed"


class CodexSourceState(StrEnum):
    UPDATED_AND_ACCEPTED = "updated_and_accepted"
    RESTORED = "restored"
    UNCHANGED = "unchanged"
    UNCERTAIN = "uncertain"


class ControlledCodexWritePipelineStage(StrEnum):
    NOT_STARTED = "not_started"
    BASELINE_CAPTURED = "baseline_captured"
    PROJECTED = "projected"
    CODEX_EXECUTED = "codex_executed"
    DELTA_VALIDATED = "delta_validated"
    SOURCE_APPLIED = "source_applied"
    FINALIZED = "finalized"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class CodexSourceApplicationResult(_StrictModel):
    attempted: bool
    applied: bool
    failure_kind: CodexWriteFailureKind | None = None
    changed_paths: list[str] = Field(default_factory=list)
    created_paths: list[str] = Field(default_factory=list)
    modified_paths: list[str] = Field(default_factory=list)
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None
    source_revalidated: bool = False
    workspace_revalidated: bool = False
    changes_accepted: bool = False
    message: str

    @field_validator("changed_paths", "created_paths", "modified_paths")
    @classmethod
    def application_paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]

    @model_validator(mode="after")
    def application_never_accepts_changes(self) -> "CodexSourceApplicationResult":
        if self.changes_accepted:
            raise ValueError("source application cannot accept changes before verification")
        return self


class CodexSourceVerificationResult(_StrictModel):
    attempted: bool
    verification_passed: bool = False
    post_verification_integrity_valid: bool = False
    finalized: bool = False
    accepted: bool = False
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None
    source_state: CodexSourceState
    failure_kind: CodexWriteFailureKind | None = None
    verification_result: object | None = None
    changed_paths: list[str] = Field(default_factory=list)
    message: str

    @field_validator("changed_paths")
    @classmethod
    def verification_paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]

    @model_validator(mode="after")
    def accepted_requires_complete_finalization(self) -> "CodexSourceVerificationResult":
        if self.accepted and not (
            self.verification_passed
            and self.post_verification_integrity_valid
            and self.finalized
            and not self.rollback_attempted
        ):
            raise ValueError("accepted source changes require finalized verified integrity")
        return self


class ControlledCodexWritePipelineResult(_StrictModel):
    attempted: bool
    accepted: bool = False
    source_state: CodexSourceState
    terminal_stage: ControlledCodexWritePipelineStage
    failure_kind: CodexWriteFailureKind | None = None
    baseline_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    workspace_created: bool = False
    workspace_cleanup_attempted: bool = False
    workspace_cleanup_succeeded: bool | None = None
    codex_result: object | None = None
    delta_result: object | None = None
    application_result: CodexSourceApplicationResult | None = None
    verification_result: CodexSourceVerificationResult | None = None
    changed_paths: list[str] = Field(default_factory=list)
    created_paths: list[str] = Field(default_factory=list)
    modified_paths: list[str] = Field(default_factory=list)
    task_changed_paths: list[str] = Field(default_factory=list)
    auxiliary_changed_paths: list[str] = Field(default_factory=list)
    message: str

    @field_validator(
        "changed_paths",
        "created_paths",
        "modified_paths",
        "task_changed_paths",
        "auxiliary_changed_paths",
    )
    @classmethod
    def pipeline_paths_are_repository_relative(cls, values: list[str]) -> list[str]:
        return [normalize_repository_relative_path(value) for value in values]

    @model_validator(mode="after")
    def accepted_requires_finalized_verification(self) -> "ControlledCodexWritePipelineResult":
        if self.accepted and not (
            self.source_state == CodexSourceState.UPDATED_AND_ACCEPTED
            and self.terminal_stage == ControlledCodexWritePipelineStage.FINALIZED
            and self.verification_result is not None
            and self.verification_result.accepted
        ):
            raise ValueError("accepted pipeline result requires finalized verification")
        return self


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
