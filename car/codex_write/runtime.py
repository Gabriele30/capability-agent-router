"""Offline controlled-write Codex runtime confined to CAR-owned B2 workspaces."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from car.escalation.handoff import render_codex_handoff_markdown

from .models import CodexWriteAuthorization, CodexWriteFailureKind, CodexWritePolicy
from .projection import ProjectedIsolatedWorkspace
from .runtime_models import (
    ControlledCodexHealthStatus,
    ControlledCodexProcessResult,
    ControlledCodexWriteHealth,
    ControlledCodexWriteRequest,
    ControlledCodexWriteResult,
)
from .workspace import IsolatedWorkspaceManager

CONTROLLED_WRITE_INSTRUCTION = (
    "Work only in the current CAR-provided isolated workspace. Make the smallest change "
    "needed within the authorized scope. Do not access or modify the source repository. "
    "Do not stage files, commit, create branches, modify Git metadata, delete or rename "
    "files, enable network access, or install dependencies. Finish with a concise summary."
)
TRUNCATION_MARKER = "\n[truncated by CAR]"
_ENVIRONMENT_KEYS = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "CODEX_HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


class ControlledCodexProcessRunner(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ControlledCodexProcessResult: ...


class SubprocessControlledCodexRunner:
    """Injectable structured subprocess boundary; it never invokes a shell."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        stdin: str,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> ControlledCodexProcessResult:
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
            return ControlledCodexProcessResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        except FileNotFoundError:
            return ControlledCodexProcessResult(executable_not_found=True)
        except subprocess.TimeoutExpired as error:
            return ControlledCodexProcessResult(
                stdout=_text(error.stdout), stderr=_text(error.stderr), timed_out=True
            )
        except OSError:
            return ControlledCodexProcessResult(stderr="controlled Codex process could not start")


class ControlledCodexWriteRuntime:
    """Run fixed Codex workspace-write only in a currently owned projected workspace.

    The instruction is a behavioral request, not the security authority. Future delta
    extraction and validation remain responsible for accepting filesystem changes.
    """

    def __init__(
        self,
        *,
        workspace_manager: IsolatedWorkspaceManager,
        runner: ControlledCodexProcessRunner | None = None,
        which: Callable[[str], str | None] | None = None,
        policy: CodexWritePolicy | None = None,
    ) -> None:
        self._workspace_manager = workspace_manager
        self._runner = runner or SubprocessControlledCodexRunner()
        self._which = which or shutil.which
        self._policy = policy or CodexWritePolicy()

    def health(self) -> ControlledCodexWriteHealth:
        """Check only the resolved local CLI login, and only when explicitly enabled."""
        if not self._policy.enabled:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.DISABLED)
        return self._health(Path.cwd())

    def execute(
        self,
        request: ControlledCodexWriteRequest,
        authorization: CodexWriteAuthorization,
    ) -> ControlledCodexWriteResult:
        if not self._policy.enabled:
            return _result(CodexWriteFailureKind.DISABLED)
        if not authorization.authorized:
            return _result(CodexWriteFailureKind.NOT_AUTHORIZED)
        workspace = request.workspace
        if not self._valid_workspace(workspace):
            return _result(CodexWriteFailureKind.INVALID_WORKSPACE)
        health = self._health(workspace.workspace.path)
        if health.status != ControlledCodexHealthStatus.READY:
            return _result(_health_failure(health.status), workspace)
        if health.executable is None:
            return _result(CodexWriteFailureKind.CODEX_NOT_READY, workspace)
        process = self._runner.run(
            _execution_argv(health.executable),
            cwd=workspace.workspace.path,
            stdin=_stdin(request),
            environment=controlled_child_environment(),
            timeout_seconds=min(
                request.timeout_seconds or self._policy.codex_write_timeout_seconds,
                self._policy.codex_write_timeout_seconds,
            ),
        )
        stdout = _truncate(process.stdout, self._policy.codex_max_stdout_chars)
        stderr = _truncate(process.stderr, self._policy.codex_max_stderr_chars)
        if process.executable_not_found:
            return _result(
                CodexWriteFailureKind.CODEX_CLI_NOT_FOUND, workspace, stdout, stderr, True
            )
        if process.timed_out:
            return _result(
                CodexWriteFailureKind.CODEX_TIMEOUT,
                workspace,
                stdout,
                stderr,
                True,
                timed_out=True,
            )
        if process.exit_code is None:
            return _result(
                CodexWriteFailureKind.CODEX_PROCESS_ERROR, workspace, stdout, stderr, True
            )
        if process.exit_code != 0:
            return _result(
                CodexWriteFailureKind.CODEX_NONZERO_EXIT,
                workspace,
                stdout,
                stderr,
                True,
                exit_code=process.exit_code,
            )
        final_message = stdout.strip() or None
        if final_message is None:
            return _result(
                CodexWriteFailureKind.CODEX_INVALID_OUTPUT,
                workspace,
                stdout,
                stderr,
                True,
                exit_code=0,
            )
        return ControlledCodexWriteResult(
            attempted=True,
            process_succeeded=True,
            final_message=final_message,
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            baseline_digest=workspace.baseline_digest,
            baseline_head_oid=workspace.baseline_head_oid,
        )

    def _health(self, cwd: Path) -> ControlledCodexWriteHealth:
        executable = self._which("codex")
        if executable is None:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.CLI_NOT_FOUND)
        process = self._runner.run(
            [executable, "login", "status"],
            cwd=cwd,
            stdin="",
            environment=controlled_child_environment(),
            timeout_seconds=self._policy.codex_login_timeout_seconds,
        )
        if process.executable_not_found:
            return ControlledCodexWriteHealth(status=ControlledCodexHealthStatus.CLI_NOT_FOUND)
        if process.timed_out:
            return ControlledCodexWriteHealth(
                status=ControlledCodexHealthStatus.UNKNOWN,
                executable=executable,
                detail="local login status check timed out",
            )
        if process.exit_code == 0:
            return ControlledCodexWriteHealth(
                status=ControlledCodexHealthStatus.READY, executable=executable
            )
        return ControlledCodexWriteHealth(
            status=ControlledCodexHealthStatus.NOT_AUTHENTICATED,
            executable=executable,
            detail="local Codex login status is not ready",
        )

    def _valid_workspace(self, projected: ProjectedIsolatedWorkspace) -> bool:
        workspace = projected.workspace
        if not projected.baseline_digest or not projected.baseline_head_oid:
            return False
        if workspace.revision != projected.baseline_head_oid:
            return False
        if workspace.path == workspace.source_root or not workspace.path.is_dir():
            return False
        return self._workspace_manager.owns(workspace)


def _execution_argv(executable: str) -> list[str]:
    return [
        executable,
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--ignore-user-config",
        CONTROLLED_WRITE_INSTRUCTION,
    ]


def controlled_child_environment(parent: dict[str, str] | None = None) -> dict[str, str]:
    """Positive allowlist for the child; credentials and arbitrary parent values are omitted."""
    source = os.environ if parent is None else parent
    by_casefold = {key.casefold(): value for key, value in source.items()}
    return {
        key: value
        for key in _ENVIRONMENT_KEYS
        if (value := by_casefold.get(key.casefold())) is not None
    }


def _stdin(request: ControlledCodexWriteRequest) -> str:
    sections = [
        "# CAR Controlled Codex Write Request",
        "\nTask:\n" + request.task,
        "\nAuthorized repository-relative paths:\n"
        + ("\n".join(f"- {path}" for path in request.authorized_paths) or "- none specified"),
        "\nThe filesystem delta remains untrusted and is not accepted by this runtime.",
    ]
    if request.handoff is not None:
        sections.append(
            "\n## Existing structured failure evidence\n"
            + render_codex_handoff_markdown(request.handoff)
        )
    return "\n".join(sections)


def _result(
    failure_kind: CodexWriteFailureKind,
    workspace: ProjectedIsolatedWorkspace | None = None,
    stdout: str = "",
    stderr: str = "",
    attempted: bool = False,
    *,
    timed_out: bool = False,
    exit_code: int | None = None,
) -> ControlledCodexWriteResult:
    return ControlledCodexWriteResult(
        attempted=attempted,
        process_succeeded=False,
        exit_code=exit_code,
        failure_kind=failure_kind,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        baseline_digest=workspace.baseline_digest if workspace is not None else None,
        baseline_head_oid=workspace.baseline_head_oid if workspace is not None else None,
    )


def _health_failure(status: ControlledCodexHealthStatus) -> CodexWriteFailureKind:
    if status == ControlledCodexHealthStatus.CLI_NOT_FOUND:
        return CodexWriteFailureKind.CODEX_CLI_NOT_FOUND
    if status == ControlledCodexHealthStatus.NOT_AUTHENTICATED:
        return CodexWriteFailureKind.CODEX_NOT_AUTHENTICATED
    return CodexWriteFailureKind.CODEX_NOT_READY


def _truncate(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[:maximum] + TRUNCATION_MARKER


def _text(value: str | bytes | None) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
