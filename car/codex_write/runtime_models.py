"""Credential-free contracts for controlled Codex execution in B2 workspaces."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from car.coding.models import normalize_repository_relative_path
from car.escalation.models import CodexHandoff
from car.telemetry.models import TokenUsage

from .models import CodexWriteFailureKind
from .projection import ProjectedIsolatedWorkspace


class _StrictRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ControlledCodexHealthStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    CLI_NOT_FOUND = "cli_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN = "unknown"


class ControlledCodexProcessResult(_StrictRuntimeModel):
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    executable_not_found: bool = False


class ControlledCodexWriteRequest(_StrictRuntimeModel):
    workspace: ProjectedIsolatedWorkspace
    task: str = Field(min_length=1, max_length=10_000)
    authorized_paths: tuple[str, ...] = ()
    handoff: CodexHandoff | None = None
    timeout_seconds: float | None = Field(default=None, gt=0, le=900)
    model: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("task")
    @classmethod
    def task_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task must not be blank")
        return value

    @field_validator("authorized_paths")
    @classmethod
    def paths_must_be_repository_relative(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_repository_relative_path(path) for path in paths)
        if len(normalized) != len(set(normalized)):
            raise ValueError("authorized paths must be unique")
        return normalized


class ControlledCodexWriteHealth(_StrictRuntimeModel):
    status: ControlledCodexHealthStatus
    executable: str | None = None
    detail: str | None = None


class ControlledCodexWriteResult(_StrictRuntimeModel):
    attempted: bool
    process_succeeded: bool
    changes_accepted: bool = False
    final_message: str | None = None
    exit_code: int | None = None
    failure_kind: CodexWriteFailureKind | None = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    usage: TokenUsage | None = None
    model: str | None = None
    baseline_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    baseline_head_oid: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")

    @model_validator(mode="after")
    def runtime_never_accepts_changes(self) -> "ControlledCodexWriteResult":
        if self.changes_accepted:
            raise ValueError("controlled runtime cannot accept source changes")
        return self
