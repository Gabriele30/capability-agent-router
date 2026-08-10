"""Read-only adapter for the user's already-authenticated local Codex CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from car.escalation.handoff import render_codex_handoff_markdown

from .models import (
    CodexExecutionRequest,
    CodexExecutionResult,
    CodexProcessResult,
    CodexRuntimeFailureKind,
    CodexRuntimeHealth,
    CodexRuntimeHealthStatus,
    CodexRuntimePolicy,
)

READ_ONLY_INSTRUCTION = (
    "Use the CAR handoff provided on stdin to analyze the failed attempt. "
    "Inspect the repository, do not modify files or make commits, and return a concise "
    "corrective plan."
)

TRUNCATION_MARKER = "\n[truncated by CAR]"


class CodexRuntime(Protocol):
    def health(self) -> CodexRuntimeHealth: ...

    def execute(self, request: CodexExecutionRequest) -> CodexExecutionResult: ...


class CodexProcessRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> CodexProcessResult: ...


class SubprocessCodexRunner:
    """Small, injectable subprocess boundary with structured argv and no shell."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> CodexProcessResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=stdin,
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            return CodexProcessResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except FileNotFoundError:
            return CodexProcessResult(executable_not_found=True)
        except subprocess.TimeoutExpired as error:
            return CodexProcessResult(
                stdout=_text(error.stdout),
                stderr=_text(error.stderr),
                timed_out=True,
            )
        except OSError:
            return CodexProcessResult(stderr="local Codex process could not start")


class LocalCodexRuntime:
    """Run only a fixed, ephemeral, read-only Codex diagnostic request."""

    def __init__(
        self,
        *,
        runner: CodexProcessRunner | None = None,
        which: Callable[[str], str | None] | None = None,
        policy: CodexRuntimePolicy | None = None,
    ) -> None:
        self._runner = runner or SubprocessCodexRunner()
        self._which = which or shutil.which
        self._policy = policy or CodexRuntimePolicy()

    def health(self) -> CodexRuntimeHealth:
        executable = self._which("codex")
        if executable is None:
            return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.CLI_NOT_FOUND)
        result = self._runner.run(
            [executable, "login", "status"],
            cwd=Path.cwd(),
            stdin="",
            environment=_child_environment(),
            timeout_seconds=self._policy.login_timeout_seconds,
        )
        if result.executable_not_found:
            return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.CLI_NOT_FOUND)
        if result.timed_out:
            return CodexRuntimeHealth(
                status=CodexRuntimeHealthStatus.UNKNOWN,
                executable=executable,
                detail="local login status check timed out",
            )
        if result.exit_code == 0:
            return CodexRuntimeHealth(status=CodexRuntimeHealthStatus.READY, executable=executable)
        return CodexRuntimeHealth(
            status=CodexRuntimeHealthStatus.NOT_AUTHENTICATED,
            executable=executable,
            detail="local Codex login status is not ready",
        )

    def execute(self, request: CodexExecutionRequest) -> CodexExecutionResult:
        root = request.repository_root.resolve()
        if not root.is_dir():
            return CodexExecutionResult(
                attempted=False,
                succeeded=False,
                failure_kind=CodexRuntimeFailureKind.INVALID_REQUEST,
            )
        health = self.health()
        if health.status != CodexRuntimeHealthStatus.READY:
            return CodexExecutionResult(
                attempted=False,
                succeeded=False,
                failure_kind=_health_failure(health.status),
            )
        if health.executable is None:
            return CodexExecutionResult(
                attempted=False,
                succeeded=False,
                failure_kind=CodexRuntimeFailureKind.UNKNOWN_ERROR,
            )
        result = self._runner.run(
            _execution_argv(health.executable),
            cwd=root,
            stdin=render_codex_handoff_markdown(request.handoff),
            environment=_child_environment(),
            timeout_seconds=request.timeout_seconds,
        )
        stdout = _truncate(result.stdout, self._policy.max_stdout_chars)
        stderr = _truncate(result.stderr, self._policy.max_stderr_chars)
        if result.executable_not_found:
            return CodexExecutionResult(
                attempted=True,
                succeeded=False,
                failure_kind=CodexRuntimeFailureKind.CLI_NOT_FOUND,
                stdout=stdout,
                stderr=stderr,
            )
        if result.timed_out:
            return CodexExecutionResult(
                attempted=True,
                succeeded=False,
                failure_kind=CodexRuntimeFailureKind.TIMEOUT,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )
        if result.exit_code is None:
            return CodexExecutionResult(
                attempted=True,
                succeeded=False,
                failure_kind=CodexRuntimeFailureKind.PROCESS_ERROR,
                stdout=stdout,
                stderr=stderr,
            )
        if result.exit_code != 0:
            return CodexExecutionResult(
                attempted=True,
                succeeded=False,
                exit_code=result.exit_code,
                failure_kind=CodexRuntimeFailureKind.NONZERO_EXIT,
                stdout=stdout,
                stderr=stderr,
            )
        final_message = stdout.strip() or None
        if final_message is None:
            return CodexExecutionResult(
                attempted=True,
                succeeded=False,
                exit_code=0,
                failure_kind=CodexRuntimeFailureKind.INVALID_OUTPUT,
                stdout=stdout,
                stderr=stderr,
            )
        return CodexExecutionResult(
            attempted=True,
            succeeded=True,
            final_message=final_message,
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
        )


def _execution_argv(executable: str) -> list[str]:
    return [
        executable,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        READ_ONLY_INSTRUCTION,
    ]


def _child_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("OPENAI_API_KEY", None)
    environment.pop("CODEX_API_KEY", None)
    return environment


def _health_failure(status: CodexRuntimeHealthStatus) -> CodexRuntimeFailureKind:
    if status == CodexRuntimeHealthStatus.CLI_NOT_FOUND:
        return CodexRuntimeFailureKind.CLI_NOT_FOUND
    if status == CodexRuntimeHealthStatus.NOT_AUTHENTICATED:
        return CodexRuntimeFailureKind.NOT_AUTHENTICATED
    return CodexRuntimeFailureKind.UNKNOWN_ERROR


def _truncate(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum] + TRUNCATION_MARKER


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""
