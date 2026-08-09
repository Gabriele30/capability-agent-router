"""Structured contracts for CAR-controlled command execution."""

from enum import StrEnum

from pydantic import BaseModel, Field

from car.router.models import Route


class ExecutionStatus(StrEnum):
    PLANNED = "planned"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


class FileChangeKind(StrEnum):
    MODIFIED = "modified"
    CREATED = "created"
    DELETED = "deleted"


class CommandSpec(BaseModel):
    args: list[str] = Field(min_length=1)
    cwd: str
    timeout_seconds: int = Field(gt=0)


class CommandResult(BaseModel):
    command: CommandSpec
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    executable_not_found: bool = False
    timed_out: bool = False


class FileChange(BaseModel):
    path: str
    kind: FileChangeKind


class ExecutionPlan(BaseModel):
    route: Route = Route.L0
    operation: str
    tool: str
    targets: list[str] = Field(min_length=1)
    commands: list[CommandSpec] = Field(min_length=1)
    verification_commands: list[CommandSpec] = Field(min_length=1)
    expected_write_scope: list[str] = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    plan: ExecutionPlan | None = None
    command_results: list[CommandResult] = Field(default_factory=list)
    verification: "VerificationResult | None" = None
    changes: list[FileChange] = Field(default_factory=list)
    rollback_attempted: bool = False
    rollback_succeeded: bool | None = None
    message: str


from car.verification.models import VerificationResult  # noqa: E402

ExecutionResult.model_rebuild()
