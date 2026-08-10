"""Typed, credential-free contracts for the local Codex CLI adapter."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from car.escalation.models import CodexHandoff


class CodexRuntimeHealthStatus(StrEnum):
    READY = "ready"
    CLI_NOT_FOUND = "cli_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    UNKNOWN = "unknown"


class CodexRuntimeFailureKind(StrEnum):
    CLI_NOT_FOUND = "cli_not_found"
    NOT_AUTHENTICATED = "not_authenticated"
    INVALID_REQUEST = "invalid_request"
    TIMEOUT = "timeout"
    PROCESS_ERROR = "process_error"
    NONZERO_EXIT = "nonzero_exit"
    INVALID_OUTPUT = "invalid_output"
    UNKNOWN_ERROR = "unknown_error"


class CodexRuntimePolicy(BaseModel):
    login_timeout_seconds: float = Field(default=10, gt=0)
    max_stdout_chars: int = Field(default=8_000, ge=100)
    max_stderr_chars: int = Field(default=4_000, ge=100)


class CodexExecutionRequest(BaseModel):
    repository_root: Path
    handoff: CodexHandoff
    timeout_seconds: float = Field(default=120, gt=0)


class CodexExecutionResult(BaseModel):
    attempted: bool
    succeeded: bool
    final_message: str | None = None
    exit_code: int | None = None
    failure_kind: CodexRuntimeFailureKind | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class CodexRuntimeHealth(BaseModel):
    status: CodexRuntimeHealthStatus
    executable: str | None = None
    detail: str | None = None


class CodexProcessResult(BaseModel):
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    executable_not_found: bool = False
