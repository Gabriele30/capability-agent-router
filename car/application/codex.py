"""Explicit, read-only application service for an already-built Codex handoff."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from car.codex.models import (
    CodexExecutionRequest,
    CodexExecutionResult,
    CodexRuntimeHealthStatus,
)
from car.codex.runtime import CodexRuntime
from car.escalation.models import CodexHandoff


class CodexApplicationFailureKind(StrEnum):
    DISABLED = "disabled"
    RUNTIME_NOT_READY = "runtime_not_ready"
    EXECUTION_FAILED = "execution_failed"
    INVALID_REQUEST = "invalid_request"


class CodexExecutionPolicy(BaseModel):
    enabled: bool = False
    timeout_seconds: float = Field(default=90, gt=0)


class CodexApplicationResult(BaseModel):
    attempted: bool
    succeeded: bool
    health_status: CodexRuntimeHealthStatus | None = None
    execution: CodexExecutionResult | None = None
    failure_kind: CodexApplicationFailureKind | None = None


def execute_codex_handoff(
    repository_root: Path,
    handoff: CodexHandoff,
    runtime: CodexRuntime,
    policy: CodexExecutionPolicy | None = None,
) -> CodexApplicationResult:
    """Execute one authorized handoff only when the caller explicitly enables it."""
    active = policy or CodexExecutionPolicy()
    if not active.enabled:
        return CodexApplicationResult(
            attempted=False,
            succeeded=False,
            failure_kind=CodexApplicationFailureKind.DISABLED,
        )
    root = repository_root.resolve()
    if not root.is_dir():
        return CodexApplicationResult(
            attempted=False,
            succeeded=False,
            failure_kind=CodexApplicationFailureKind.INVALID_REQUEST,
        )
    health = runtime.health()
    if health.status != CodexRuntimeHealthStatus.READY:
        return CodexApplicationResult(
            attempted=False,
            succeeded=False,
            health_status=health.status,
            failure_kind=CodexApplicationFailureKind.RUNTIME_NOT_READY,
        )
    execution = runtime.execute(
        CodexExecutionRequest(
            repository_root=root,
            handoff=handoff,
            timeout_seconds=active.timeout_seconds,
        )
    )
    return CodexApplicationResult(
        attempted=True,
        succeeded=execution.succeeded,
        health_status=health.status,
        execution=execution,
        failure_kind=None if execution.succeeded else CodexApplicationFailureKind.EXECUTION_FAILED,
    )
